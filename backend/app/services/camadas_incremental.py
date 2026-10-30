"""Parte 2: prata/ouro incrementais (decisões do TL, 30/10).

Núcleo: bronze.ultima_versao (versão mais recente SEM quarentena de cada chave). Por (sistema, tabela) a marca
d'água (controle.camadas_marca) guarda até que registro_id já foi expandido em pendências e já foi aplicado.

1. Modo. Recomposição completa (caminho de sempre) quando: pedida pelo admin; primeira vez (sem marca); regras,
   parâmetros ou confirmações de óbito mudaram; ou mudou alguma fonte de PESSOAS ou dimensão dos fatos (a correlação
   é recomposta inteira e os cidadao_id podem mudar). Senão, incremental.
2. Expansão (commit por faixa): chaves com registro_id > expandido_ate viram pendências (tipo, chave).
3. Aplicação (commit por lote, checkpoint = a própria fila): tributário por CODI/lançamento/dívida/SIL, fatos da
   Saúde por chave, depois eventos por cidadão afetado. Ouro (visão e indicadores) recomposto INTEIRO no fim, numa
   transação (D7: ouro incremental fica para a Parte 3).
   Cancelar/cair: os lotes confirmados ficam; a retomada continua da fila. A execução segue "em andamento" até o fim.
Exclusões: como hoje, não detectadas (decisão 3 do TL). Só chaves e contagens nos logs: nenhum dado pessoal.
"""
import hashlib
import json

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.ingestion.mapeamentos import MAPEAMENTOS
from app.models.camadas import UltimaVersao
from app.models.controle import CamadasMarca, CamadasPendencia, ConfirmacaoObito, Parametro

LOTE = 2000
LOTE_IDS = 200_000
REGRAS = ("_regras", "_")

TRIB = {("IPTU", "Cadastro"): "codi", ("IPTU", "Cadastro_Possuidor"): "codi",
        ("FINANCEIRO", "Financeiro_Exercicio"): "lanc", ("DIVIDA", "Divida"): "div", ("SIL", "Empresas"): "emp"}
FATOS = {("SAUDE", "Consultas"): "atend", ("SAUDE", "Agendamento_Consulta"): "agend", ("SAUDE", "Vacina"): "vac"}
DIMENSOES = {("SAUDE", "Unidades"), ("SAUDE", "Aux_Vacina"), ("SAUDE", "Aux_Especialidades")}
FORA = {"GESTAOESCOLAR", "CORESSO"}  # não entram na prata (TR3)
ORDEM = ("codi", "lanc", "div", "emp", "atend", "agend", "vac", "evento")  # D7: ouro inteiro (ouro incremental = Parte 3)


class Cancelado(Exception):
    """Pedido de cancelamento entre lotes: os lotes já confirmados ficam, a fila continua na retomada."""


def classe(sistema: str, tabela: str) -> str:
    if (sistema, tabela) in TRIB:
        return "trib"
    if (sistema, tabela) in FATOS:
        return "fato"
    if (sistema, tabela) in DIMENSOES:
        return "dim"
    m = MAPEAMENTOS.get((sistema, tabela))
    if sistema in FORA or not m or m.get("papel") == "fato":
        return "fora"
    return "pessoas"


def assinatura_regras(db: Session) -> str:
    from app.services.camadas_estado import hash_regras
    conf = db.execute(select(func.count(), func.max(ConfirmacaoObito.id))).one()
    params = sorted((p.chave, p.valor) for p in db.scalars(select(Parametro)))
    return hashlib.sha256(json.dumps([hash_regras(), list(conf), params], default=str).encode()).hexdigest()


def _maximos(db: Session, ate: int) -> dict[tuple[str, str], int]:
    U = UltimaVersao
    return {(s, t): int(m) for s, t, m in db.execute(
        select(U.sistema_origem, U.tabela, func.max(U.registro_id)).where(U.registro_id <= ate)
        .group_by(U.sistema_origem, U.tabela))}


def _marcas(db: Session) -> dict[tuple[str, str], CamadasMarca]:
    return {(m.sistema, m.tabela): m for m in db.scalars(select(CamadasMarca)) if not m.sistema.startswith("_")}


def decide(db: Session, ate: int, recompor: bool = False) -> tuple[str, str]:
    """('recomposicao' | 'incremental', motivo). Motivo só com nomes de tabela, sem dado."""
    if recompor:
        return "recomposicao", "pedida pelo administrador"
    r = db.get(CamadasMarca, REGRAS)
    if r is None:
        return "recomposicao", "primeira execução (sem marca d'água)"
    if r.assinatura != assinatura_regras(db):
        return "recomposicao", "regras, parâmetros ou confirmações mudaram"
    marcas = _marcas(db)
    for st, mx in _maximos(db, ate).items():
        c = classe(*st)
        m = marcas.get(st)
        if c in ("pessoas", "dim") and (m is None or mx > m.expandido_ate):
            return "recomposicao", f"mudou fonte de {'pessoas' if c == 'pessoas' else 'dimensão'} {st[0]}.{st[1]}"
    return "incremental", "só fatos/tributário mudaram"


def marca_tudo(db: Session, ate: int, ex_id: int | None) -> None:
    """Depois de uma recomposição completa (mesma transação): marcas no máximo <= ate e fila vazia."""
    db.execute(delete(CamadasPendencia))
    marcas = _marcas(db)
    for (s, t), mx in _maximos(db, ate).items():
        m = marcas.get((s, t)) or CamadasMarca(sistema=s, tabela=t)
        m.ultimo_id = m.expandido_ate = mx
        m.execucao_id = ex_id
        db.add(m)
    r = db.get(CamadasMarca, REGRAS) or CamadasMarca(sistema=REGRAS[0], tabela=REGRAS[1], ultimo_id=0, expandido_ate=0)
    r.assinatura, r.ultimo_id, r.execucao_id = assinatura_regras(db), ate, ex_id
    db.add(r)


_CHAVE = {"codi": "left(btrim(r.dados->>'CODI'), 22)"}


def _expande(db: Session, ate: int, ex_id, pulso) -> dict:
    """Chaves novas/alteradas viram pendências, por faixa de registro_id (commit por faixa: retomável)."""
    cont = {"novas": 0, "alteradas": 0}
    marcas = _marcas(db)
    for (s, t), mx in sorted(_maximos(db, ate).items()):
        c = classe(s, t)
        m = marcas.get((s, t))
        if m is None:
            m = CamadasMarca(sistema=s, tabela=t, ultimo_id=0, expandido_ate=0)
            db.add(m)
            db.flush()
        if c not in ("trib", "fato"):  # "fora": nada a expandir; pessoas/dim aqui já teriam forçado recomposição
            m.expandido_ate = max(m.expandido_ate, mx)
            db.commit()
            continue
        tipo = TRIB.get((s, t)) or FATOS[(s, t)]
        while m.expandido_ate < mx:
            de, a = m.expandido_ate, min(m.expandido_ate + LOTE_IDS, mx)
            chave = _CHAVE.get(tipo, "u.id_origem")
            if tipo == "emp":
                chave = "'*'"
            p = {"s": s, "t": t, "de": de, "a": a, "tipo": tipo}
            filtro = ("FROM bronze.ultima_versao u JOIN bronze.registros r ON r.id = u.registro_id "
                      "WHERE u.sistema_origem = :s AND u.tabela = :t AND u.registro_id > :de AND u.registro_id <= :a")
            db.execute(text(f"INSERT INTO controle.camadas_pendencias (tipo, chave) SELECT DISTINCT :tipo, {chave} "
                            f"{filtro} AND {chave} IS NOT NULL ON CONFLICT DO NOTHING"), p)
            tot, alt = db.execute(text(
                "SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM bronze.registros o WHERE "
                "o.sistema_origem = u.sistema_origem AND o.tabela = u.tabela AND o.id_origem = u.id_origem AND "
                f"o.quarentena IS NULL AND o.id <= :de)) {filtro}"), p).one()
            cont["alteradas"] += int(alt)
            cont["novas"] += int(tot) - int(alt)
            m.expandido_ate, m.execucao_id = a, ex_id
            db.commit()
            if pulso:
                pulso()
    return cont


def _enfileira(db: Session, tipo: str, chaves) -> None:
    from sqlalchemy.dialects.postgresql import insert
    vals = [{"tipo": tipo, "chave": str(c)} for c in chaves if c is not None]
    for i in range(0, len(vals), 5000):
        db.execute(insert(CamadasPendencia).values(vals[i:i + 5000]).on_conflict_do_nothing())


def _aplica(db: Session, tipo: str, chaves: list[str]) -> None:
    from app.silver import fatos, tributario
    cids: set = set()
    if tipo == "codi":
        cids = tributario.recalcula_codis(db, set(chaves))
    elif tipo == "lanc":
        cids = tributario.recalcula_lancamentos(db, set(chaves))
    elif tipo == "div":
        cids = tributario.recalcula_dividas(db, set(chaves))
    elif tipo == "emp":
        cids = tributario.recalcula_empresas(db)
    elif tipo in ("atend", "agend", "vac"):
        cids = fatos.recalcula_fatos(db, tipo, set(chaves))
        _enfileira(db, "evento", cids)
        cids = set()
    elif tipo == "evento":
        fatos.recalcula_eventos(db, set(chaves))
    return cids


def _pendentes(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(CamadasPendencia)) or 0)


def executa(db: Session, ate: int, ex_id: int | None = None, cancelado=None, progresso=None,
            ouro=None, confere=None) -> dict:
    """confere(db): checagem da prata antes do ouro (CC11, carga_completa.confere_prata); se subir, o ouro não roda."""
    """Incremental: expande, aplica a fila por lote (commit por lote) e fecha as marcas. Sobe Cancelado entre lotes."""
    from app.gold import indicadores, visao

    def checa():
        if cancelado is not None and cancelado():
            raise Cancelado()
    cont = _expande(db, ate, ex_id, checa)
    total = _pendentes(db)
    feitos = 0
    por_tipo: dict[str, int] = {}
    if progresso and total:  # sem fila não manda "0 de 0" (a tela mantém o sinal de vida do Pulso)
        progresso(0, total)
    for tipo in ORDEM:
        while True:
            checa()
            chaves = list(db.scalars(select(CamadasPendencia.chave).where(CamadasPendencia.tipo == tipo)
                                     .order_by(CamadasPendencia.chave).limit(LOTE)))
            if not chaves:
                break
            _aplica(db, tipo, chaves)
            db.execute(delete(CamadasPendencia).where(CamadasPendencia.tipo == tipo,
                                                      CamadasPendencia.chave.in_(chaves)))
            db.commit()  # checkpoint: o lote saiu da fila junto com o resultado dele
            feitos += len(chaves)
            por_tipo[tipo] = por_tipo.get(tipo, 0) + len(chaves)
            total = max(total, feitos + _pendentes(db))  # eventos/ouro entram na fila durante a aplicação
            if progresso:
                progresso(feitos, total)
    checa()
    if confere is not None:
        confere(db)  # CC11 antes do ouro, sempre (barato: só contagens)
    gres = None
    # ouro roda se alguma marca foi expandida e ainda não fechada (inclui rodada anterior interrompida antes do ouro)
    if any(m.ultimo_id < m.expandido_ate for m in _marcas(db).values()):  # D7: ouro inteiro, como hoje, numa transação (cancelado = rollback do ouro; a prata já confirmada fica)
        from app.silver.processamento import CamadasCanceladas
        try:
            gres = visao.reconstruir(db, progresso=ouro, cancelado=cancelado)
        except CamadasCanceladas:
            raise Cancelado() from None
        indicadores.reconstruir(db)
    for m in _marcas(db).values():
        m.ultimo_id = m.expandido_ate
    db.commit()
    return {**cont, "excluidas": 0, "recalculadas": por_tipo, "lotes_chaves": feitos, "ouro": gres}
