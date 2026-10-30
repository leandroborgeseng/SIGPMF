"""Gold: visão por cidadão e domínio para o futuro Portal do Cidadão.
Quando as fontes divergem, a gold mostra o valor da fonte com a data de atualização mais recente
(as demais versões continuam na silver/bronze). Cada valor guarda fonte e data. CPF não vai para a gold.

Desempenho (27/10, ouro travando com ~500 mil registros): a versão antiga carregava TODOS os registros da bronze
ligados a vínculos e TODOS os vínculos como objetos ORM, montava um DadoGold ORM por campo (milhões de objetos na
sessão) e inseria tudo num único flush, sem sinal de vida. Agora:
- os vínculos vêm em streaming (cursor no servidor, yield_per), ordenados por (cidadao_id, id): o estado em memória é
  só o do cidadão atual, e a regra "fonte mais recente vence; empate fica com o primeiro vínculo (id)" é a mesma;
- os dados vão por INSERT em lote (Core, sem ORM) a cada GOLD_LOTE linhas, com progresso(processados, total);
- gold.cidadao_sistemas sai de um único INSERT ... SELECT com GROUP BY (set-based, sem subconsulta por cidadão), na
  mesma ordem de sistemas/domínios (COLLATE "C" = sorted() do Python).
`reconstruir_legado` fica só como referência do teste de regressão (mesmo resultado)."""
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.orm import Session

from app.ingestion.mapeamentos import canonico
from app.models.camadas import Cidadao, CidadaoSistemas, DadoGold, RegistroBronze, SistemaSilver, Vinculo
from app.util.datas import normaliza_data_iso

DOMINIO_CAMPO = {"nome": "identificacao", "data_nascimento": "identificacao", "nome_mae": "identificacao",
                 "nome_pai": "identificacao", "sexo": "identificacao", "data_obito": "identificacao",
                 "endereco": "endereco"}
CAMPOS_DATA = {"data_nascimento", "data_obito"}
FORA_DA_GOLD = {"cpf", "cns", "atualizado_em", "chave_pessoa", "pista_normalizado", "pista_matricula_correta"}
GOLD_LOTE = 5000

_SQL_CIDADAO_SISTEMAS = text("""
INSERT INTO gold.cidadao_sistemas (cidadao_id, nome, sistemas, dominios, atualizado_em)
SELECT c.cidadao_id, c.nome, COALESCE(s.sistemas, '[]'::jsonb), COALESCE(d.dominios, '[]'::jsonb), c.atualizado_em
  FROM silver.cidadaos c
  LEFT JOIN (SELECT cidadao_id, jsonb_agg(sistema ORDER BY sistema COLLATE "C") AS sistemas
               FROM (SELECT DISTINCT cidadao_id, sistema FROM silver.vinculos) x
              GROUP BY cidadao_id) s ON s.cidadao_id = c.cidadao_id
  LEFT JOIN (SELECT cidadao_id, jsonb_agg(dominio ORDER BY dominio COLLATE "C") AS dominios
               FROM (SELECT DISTINCT v.cidadao_id, COALESCE(NULLIF(ss.dominio, ''), 'outros') AS dominio
                       FROM silver.vinculos v LEFT JOIN silver.sistemas ss ON ss.codigo = v.sistema) y
              GROUP BY cidadao_id) d ON d.cidadao_id = c.cidadao_id
""")


def _mais_novo(v_data, atual_data) -> bool:
    return bool(v_data and (atual_data is None or v_data > atual_data))


def reconstruir(db: Session, progresso=None, cancelado=None) -> dict[str, int]:
    """progresso(processados, total): sinal de vida a cada lote (vínculos processados / total de vínculos) e no
    mínimo a cada Pulso.PULSO_S segundos. cancelado(): checado a cada lote; se True, sobe CamadasCanceladas e quem
    chama faz rollback (ouro anterior intacto, nada parcial)."""
    from app.silver.processamento import Pulso
    pulso = Pulso(progresso, cancelado)
    db.execute(delete(DadoGold))
    db.execute(delete(CidadaoSistemas))
    db.flush()
    dominios = {s.codigo: s.dominio or "outros" for s in db.scalars(select(SistemaSilver))}
    falecidos = {c.cidadao_id: (c.obito_fonte or "CEMITERIO").split(".")[0]
                 for c in db.execute(select(Cidadao.cidadao_id, Cidadao.obito_fonte).where(Cidadao.falecido.is_(True)))}
    total = int(db.scalar(select(func.count()).select_from(Vinculo)) or 0)
    tabela = DadoGold.__table__
    buf: list[dict] = []
    gravados = [0]

    def descarrega(forca: bool = False) -> None:
        if buf and (forca or len(buf) >= GOLD_LOTE):
            db.execute(insert(tabela), buf)
            gravados[0] += len(buf)
            buf.clear()

    def fecha_cidadao(cid: str, melhor: dict) -> None:
        if cid in falecidos:
            melhor["falecido"] = {"cidadao_id": cid, "dominio": "identificacao", "campo": "falecido",
                                  "valor": "sim (confirmado)", "sistema": falecidos.pop(cid),
                                  "fonte_atualizado_em": None, "mais_recente": True}
        buf.extend(melhor.values())
        descarrega()

    q = (select(Vinculo.cidadao_id, Vinculo.sistema, Vinculo.chave_origem, Vinculo.fonte_atualizado_em,
                RegistroBronze.sistema_origem, RegistroBronze.tabela, RegistroBronze.dados)
         .join(RegistroBronze, RegistroBronze.id == Vinculo.bronze_id)
         .order_by(Vinculo.cidadao_id, Vinculo.id)
         .execution_options(yield_per=GOLD_LOTE))
    atual_cid, melhor, feitos = None, {}, 0
    for cid, sistema, chave, fonte_em, s_origem, tab, dados in db.execute(q):
        if cid != atual_cid:
            if atual_cid is not None:
                fecha_cidadao(atual_cid, melhor)
            atual_cid, melhor = cid, {}
        for campo, valor in canonico(s_origem, tab, dados).items():
            if campo in CAMPOS_DATA:
                valor = normaliza_data_iso(valor)
            if campo in FORA_DA_GOLD or valor is None:
                continue
            atual = melhor.get(campo)
            if atual is None or _mais_novo(fonte_em, atual["fonte_atualizado_em"]):
                melhor[campo] = {"cidadao_id": cid, "dominio": DOMINIO_CAMPO.get(campo, "outros"), "campo": campo,
                                 "valor": str(valor), "sistema": sistema, "fonte_atualizado_em": fonte_em,
                                 "mais_recente": True}
        # presença do cidadão no domínio do sistema (o último vínculo do sistema vence, como antes)
        melhor[f"cadastro_{sistema}"] = {"cidadao_id": cid, "dominio": dominios.get(sistema, "outros"),
                                         "campo": "cadastro", "valor": f"{tab}:{chave}", "sistema": sistema,
                                         "fonte_atualizado_em": fonte_em, "mais_recente": True}
        feitos += 1
        if feitos % GOLD_LOTE == 0:
            pulso.lote(feitos, total)
        else:
            pulso.pulsa(feitos, total)
    if atual_cid is not None:
        fecha_cidadao(atual_cid, melhor)
    for cid in sorted(falecidos):  # falecido sem vínculo (raro): só a marca de óbito
        fecha_cidadao(cid, {})
    descarrega(forca=True)
    db.execute(_SQL_CIDADAO_SISTEMAS)
    if progresso:
        progresso(feitos, total)
    return {"dados": gravados[0]}


def reconstruir_legado(db: Session) -> dict[str, int]:
    """Algoritmo antigo (ORM, tudo em memória). Só para o teste de regressão: mesmo resultado que reconstruir()."""
    from app.silver.processamento import exige_ambiente_teste
    exige_ambiente_teste("ouro legado")
    db.execute(delete(DadoGold))
    db.execute(delete(CidadaoSistemas))
    dominios = {s.codigo: s.dominio or "outros" for s in db.scalars(select(SistemaSilver))}
    regs = {r.id: r for r in db.scalars(select(RegistroBronze).where(RegistroBronze.id.in_(select(Vinculo.bronze_id))))}
    melhor: dict[tuple[str, str], DadoGold] = {}
    sistemas: dict[str, set[str]] = {}
    for v in db.scalars(select(Vinculo).order_by(Vinculo.id)):
        r = regs[v.bronze_id]
        sistemas.setdefault(v.cidadao_id, set()).add(v.sistema)
        c = canonico(r.sistema_origem, r.tabela, r.dados)
        for campo, valor in c.items():
            if campo in CAMPOS_DATA:
                valor = normaliza_data_iso(valor)
            if campo in FORA_DA_GOLD or valor is None:
                continue
            k = (v.cidadao_id, campo)
            atual = melhor.get(k)
            if atual is None or (v.fonte_atualizado_em and (atual.fonte_atualizado_em is None or v.fonte_atualizado_em > atual.fonte_atualizado_em)):
                melhor[k] = DadoGold(cidadao_id=v.cidadao_id, dominio=DOMINIO_CAMPO.get(campo, "outros"), campo=campo,
                                     valor=str(valor), sistema=v.sistema, fonte_atualizado_em=v.fonte_atualizado_em,
                                     mais_recente=True)
        k = (v.cidadao_id, f"cadastro_{v.sistema}")
        melhor[k] = DadoGold(cidadao_id=v.cidadao_id, dominio=dominios.get(v.sistema, "outros"), campo="cadastro",
                             valor=f"{r.tabela}:{v.chave_origem}", sistema=v.sistema,
                             fonte_atualizado_em=v.fonte_atualizado_em, mais_recente=True)
    for c in db.scalars(select(Cidadao).where(Cidadao.falecido.is_(True))):
        sis = (c.obito_fonte or "CEMITERIO").split(".")[0]
        melhor[(c.cidadao_id, "falecido")] = DadoGold(cidadao_id=c.cidadao_id, dominio="identificacao", campo="falecido",
                                                      valor="sim (confirmado)", sistema=sis, fonte_atualizado_em=None,
                                                      mais_recente=True)
    db.add_all(melhor.values())
    for c in db.scalars(select(Cidadao)):
        sis = sorted(sistemas.get(c.cidadao_id, set()))
        db.add(CidadaoSistemas(cidadao_id=c.cidadao_id, nome=c.nome, sistemas=sis,
                               dominios=sorted({dominios.get(s, "outros") for s in sis}), atualizado_em=c.atualizado_em))
    db.flush()
    return {"dados": len(melhor)}
