"""Carga do conector de educação (GE3/GE8/GE11): amostra primeiro, depois carga completa com hash por linha.

- Amostra: TOP (SIGPMF_GESTAOESCOLAR_AMOSTRA, máx. 50) de cada fonte; grava na bronze. Sem amostra concluída com
  sucesso, a carga completa é recusada.
- Completa: só dentro da janela noturna (AGENDA_JANELA, fuso AGENDA_FUSO), salvo SIGPMF_EDUCACAO_JANELA_NOTURNA=false
  (padrão true); com a trava desligada, a execução registra no log que a janela foi ignorada. Keyset pela PK de cada fonte com
  checkpoint (controle.checkpoints_carga) e teto de lotes por execução (SIGPMF_GESTAOESCOLAR_MAX_LOTES, padrão 1):
  atingido o teto, a execução termina como `parcial` e a próxima continua do checkpoint, sem lacuna nem duplicata.
- Hash por linha e por tabela (bronze.ingere): documento, certidão e endereço têm hash próprio, porque mudar um
  documento não muda `pes_dataAlteracao`.
- Ciclo completo (todas as fontes concluídas): reconstrói a prata da educação e volta o keyset ao início, mas a
  marca d'água (`checkpoints_carga.marca_dagua`, IN1) NÃO é zerada.
- Incremental (IN2/IN6/IN9, padrão da agenda): cada fonte com `campo_data` lê só `campo_data >= marca - margem`
  (CARGA_INCREMENTAL_MARGEM_H, 24 h), mais as `datas_extra` (END_Endereco) e as pessoas que entraram no escopo.
  Sem marca, usa a maior data já gravada na bronze; fonte sem campo_data (8 linhas) é relida inteira. Sem linha
  nova ou alterada, a prata não é refeita ("Concluída · nenhuma linha nova"). A completa fica só manual (ADMIN).
- Nada é apagado: a bronze é append-only (versões por hash) e a prata é upsert.
- Mensagens e logs só com contagens e nomes de tabela; nunca credencial, nome, CPF ou id de pessoa.
"""
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.bronze.ingestao import ingere
from app.connectors.gestaoescolar.consultas import FONTES, MSG_SEM_TIPO_DOCUMENTO, ConfigGE, Fonte, TiposDocumentoAusentes
from app.core.config import get_settings
from app.core.db import get_engine
from app.models.controle import CheckpointCarga, Execucao, Log
from app.services import agenda, tabelas_vazias
from app.services import trava as tv
from app.services.diagnostico import linha_json
from app.silver import educacao

_log = logging.getLogger(__name__)
FATIA_PAUSA_S = 0.1
BATIDA_PROCESSA_S = 30.0
TIPO_AMOSTRA = "amostra_gestaoescolar"
TIPO_COMPLETA = "carga_gestaoescolar"
SISTEMA = "GESTAOESCOLAR"
SEM_AMOSTRA = "Rode a amostra do conector de educação com sucesso antes da carga completa."
EM_ANDAMENTO = "Já existe uma carga completa do conector de educação em andamento."
NADA_EM_ANDAMENTO = "Nenhuma carga completa do conector de educação em andamento."
BATIDA_RECENTE = "A execução teve atividade nos últimos {min} min; aguarde ou cancele."
FORA_JANELA = "Carga completa do conector de educação só roda na janela noturna ({janela})."


MAX_MENSAGEM = 300
SEM_CREDENCIAL = ("Credencial do conector de educação não configurada ou recusada "
                  "(verifique SIGPMF_SRC_GESTAOESCOLAR_*).")


class CargaRecusada(RuntimeError):
    """Recusa com motivo fixo: `codigo` em ("sem_amostra", "fora_janela", "em_andamento", "nada_em_andamento")."""

    def __init__(self, mensagem: str, codigo: str = "recusada") -> None:
        super().__init__(mensagem)
        self.codigo = codigo


def corta(texto: str | None, limite: int = MAX_MENSAGEM) -> str | None:
    if texto is None or len(texto) <= limite:
        return texto
    return texto[: limite - 1] + "…"


def mensagem_segura(exc: BaseException) -> str:
    """Texto fixo por tipo de erro (nunca str(exc): pode trazer host, usuário ou senha), até MAX_MENSAGEM."""
    if isinstance(exc, CargaRecusada):
        if exc.codigo == "sem_amostra":
            return corta(SEM_AMOSTRA)
        if exc.codigo == "em_andamento":
            return corta(EM_ANDAMENTO)
        if exc.codigo == "nada_em_andamento":
            return corta(NADA_EM_ANDAMENTO)
        if exc.codigo == "fora_janela":
            return corta(FORA_JANELA.format(janela=get_settings().agenda_janela))
        return corta("Carga do conector de educação recusada.")
    if isinstance(exc, PermissionError):
        return corta(SEM_CREDENCIAL)
    return corta(f"Falha no conector de educação ({type(exc).__name__}).")


def _chave(f: Fonte, r: dict) -> list:
    return [None if r.get(k) is None else (r[k] if isinstance(r[k], int) else str(r[k])) for k in f.chave]


def _nova_execucao(db: Session, tipo: str, usuario: str | None, modo: str) -> Execucao:
    ex = Execucao(tipo=tipo, sistema=SISTEMA, status="em_andamento", usuario=usuario, modo=modo,
                  atualizado_em=datetime.now(UTC))
    db.add(ex)
    db.commit()
    return ex


def _ingere(db: Session, f: Fonte, linhas: list[dict], ex: Execucao) -> dict:
    linhas = [linha_json(r) for r in linhas]  # UUID, datas e Decimal do pymssql -> JSON
    return ingere(db, f.sistema, f.nome, f.chave if len(f.chave) > 1 else f.chave[0], f.campo_data, linhas, ex.id,
                  lote=max(1, len(linhas)), confirmar=False)


def _soma(ex: Execucao, c: dict) -> None:
    for k in ("lidos", "novos", "atualizados", "ignorados", "lotes"):
        setattr(ex, k, (getattr(ex, k) or 0) + c.get(k, 0))


def amostra_ok(db: Session) -> bool:
    ult = db.scalar(select(Execucao).where(Execucao.tipo == TIPO_AMOSTRA).order_by(Execucao.id.desc()).limit(1))
    return bool(ult and ult.status == "sucesso" and (ult.lidos or 0) > 0)


def dentro_da_janela(agora: datetime) -> tuple[bool, str]:
    s = get_settings()
    ini, fim = agenda.janela(s.agenda_janela)
    local = agora.astimezone(agenda._fuso())
    return agenda.na_janela(local.hour * 60 + local.minute, ini, fim), s.agenda_janela


def janela_exigida() -> bool:
    """SIGPMF_EDUCACAO_JANELA_NOTURNA (padrão true). Só afeta a carga completa da educação; o SEI não usa isto."""
    valor = get_settings().ge_janela_noturna
    return not (isinstance(valor, str) and valor.strip().lower() == "false")


def checa_janela(agora: datetime) -> bool:
    """Recusa fora da janela quando a trava está ligada. Devolve True se a checagem foi ignorada (trava desligada)."""
    if not janela_exigida():
        return True
    ok, jan = dentro_da_janela(agora)
    if not ok:
        raise CargaRecusada(FORA_JANELA.format(janela=jan), "fora_janela")
    return False


def completa_em_andamento(db: Session) -> bool:
    return db.scalar(select(Execucao.id).where(Execucao.tipo == TIPO_COMPLETA, Execucao.status == "em_andamento")
                     .limit(1)) is not None


def solicita_cancelamento(db: Session) -> Execucao:
    """Marca o pedido (coluna `cancelar`, 0008); a carga para no fim do lote atual. Nada é apagado."""
    ex = db.scalar(select(Execucao).where(Execucao.tipo == TIPO_COMPLETA, Execucao.status == "em_andamento")
                   .order_by(Execucao.id.desc()).limit(1))
    if ex is None:
        raise CargaRecusada(NADA_EM_ANDAMENTO, "nada_em_andamento")
    ja = bool(ex.cancelar)
    ex.cancelar = True
    db.commit()
    ex.ja_solicitado = ja  # atributo transitório (não é coluna)
    return ex


def marca_falha_orfa(db: Session, eid: int, agora: datetime | None = None) -> bool:
    """UPDATE atômico: só vira `falha` se ainda está em_andamento e sem batida (atualizado_em) há mais de N min."""
    agora = agora or datetime.now(UTC)
    limite = agora - timedelta(minutes=int(get_settings().ge_orfa_min))
    r = db.execute(update(Execucao).where(
        Execucao.id == eid, Execucao.tipo == TIPO_COMPLETA, Execucao.status == "em_andamento",
        (Execucao.atualizado_em.is_(None)) | (Execucao.atualizado_em < limite),
    ).values(status="falha", finalizado_em=agora,
             mensagem=f"Marcada como falha pelo ADMIN: sem atividade há mais de {get_settings().ge_orfa_min} min."))
    db.commit()
    return r.rowcount == 1


def amostra(db: Session, leitor, cfg: ConfigGE, usuario: str | None = None) -> Execucao:
    ex = _nova_execucao(db, TIPO_AMOSTRA, usuario, "amostra")
    try:
        for f in FONTES:
            linhas = leitor.ler(f, None, min(int(cfg.amostra), 50))
            _soma(ex, _ingere(db, f, linhas, ex))
        ex.status, ex.mensagem = "sucesso", f"Amostra: {ex.lidos} linhas lidas em {len(FONTES)} tabelas."
    except TiposDocumentoAusentes as exc:  # motivo fixo, sem o texto da exceção
        db.rollback()
        ex = db.get(Execucao, ex.id)
        ex.status, ex.mensagem, ex.erro_classe = "falha", f"Amostra falhou: {MSG_SEM_TIPO_DOCUMENTO}", exc.codigo
    except Exception as exc:  # noqa: BLE001 - só a classe do erro (sem host/usuário)
        db.rollback()
        ex = db.get(Execucao, ex.id)
        ex.status, ex.mensagem = "falha", f"Amostra falhou ({type(exc).__name__})."
    ex.finalizado_em = datetime.now(UTC)
    db.commit()
    return ex


def _checkpoint(db: Session, f: Fonte) -> CheckpointCarga:
    ck = db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == f.sistema, CheckpointCarga.tabela == f.nome))
    if ck is None:
        ck = CheckpointCarga(sistema=f.sistema, tabela=f.nome, posicao=0, ultima_chave=None, concluida=False)
        db.add(ck)
        db.flush()
    return ck


def pausa_lote_ms() -> int:
    """SIGPMF_GESTAOESCOLAR_PAUSA_LOTE_MS: ausente/0 = sem pausa (como antes); inválido ou negativo = 0 com WARNING."""
    bruto = get_settings().ge_pausa_lote_ms
    if bruto is None or not str(bruto).strip():
        return 0
    try:
        v = int(str(bruto).strip())
    except ValueError:
        v = -1
    if v < 0:
        _log.warning("SIGPMF_GESTAOESCOLAR_PAUSA_LOTE_MS inválida; usando 0 (sem pausa).")
        return 0
    return v


def _cancelamento_pedido(ex_id: int) -> bool:
    """Lê o pedido numa conexão separada (pedido de outra requisição, ou execução já interrompida/marcada falha)."""
    with get_engine().connect() as c:
        r = c.execute(select(Execucao.cancelar, Execucao.status).where(Execucao.id == ex_id)).first()
    return r is None or bool(r[0]) or r[1] != "em_andamento"


def _pausa(ex_id: int, ms: int) -> bool:
    """Dorme em fatias curtas checando o cancelamento; batida antes, durante (a cada ~1 s) e depois. True = cancelada."""
    if ms <= 0:
        return False
    tv.sinal_de_vida(ex_id)
    fim = time.monotonic() + ms / 1000
    ultima_batida = time.monotonic()
    while (falta := fim - time.monotonic()) > 0:
        time.sleep(min(FATIA_PAUSA_S, falta))
        if _cancelamento_pedido(ex_id):
            return True
        if time.monotonic() - ultima_batida >= 1:
            tv.sinal_de_vida(ex_id)
            ultima_batida = time.monotonic()
    tv.sinal_de_vida(ex_id)
    return False


class _BatidaEmFundo:
    """Batida periódica (conexão separada) enquanto a etapa final (educacao.processa) roda."""

    def __init__(self, ex_id: int, intervalo_s: float):
        self.ex_id, self.intervalo_s, self.parar = ex_id, intervalo_s, threading.Event()
        self.t = threading.Thread(target=self._roda, daemon=True)

    def _roda(self):
        while not self.parar.wait(self.intervalo_s):
            try:
                tv.sinal_de_vida(self.ex_id)
            except Exception:  # noqa: BLE001 - batida é best-effort
                _log.warning("batida da carga da educação falhou")

    def __enter__(self):
        tv.sinal_de_vida(self.ex_id)
        self.t.start()
        return self

    def __exit__(self, *a):
        self.parar.set()
        self.t.join()
        tv.sinal_de_vida(self.ex_id)
        return False


def _trava(db: Session):
    """Advisory lock de sessão do sistema (mesma chave da CC12), numa conexão própria, segurado a carga inteira:
    morre com o processo, então o startup e o /interromper sabem se há processo vivo."""
    conn = get_engine().connect()
    if not conn.execute(text("select pg_try_advisory_lock(:k)"), {"k": tv.chave_lock(SISTEMA)}).scalar():
        conn.close()
        return None
    conn.commit()
    return conn


def _solta(conn) -> None:
    try:
        conn.execute(text("select pg_advisory_unlock(:k)"), {"k": tv.chave_lock(SISTEMA)})
        conn.commit()
    finally:
        conn.close()


def _maior_bronze(db: Session, f: Fonte):
    """Maior data de alteração já gravada na bronze da fonte (sem fuso, como a origem), ou None."""
    from sqlalchemy import func

    from app.models.camadas import RegistroBronze
    maior = db.scalar(select(func.max(RegistroBronze.atualizado_origem_em)).where(
        RegistroBronze.sistema_origem == f.sistema, RegistroBronze.tabela == f.nome))
    return None if maior is None else maior.replace(tzinfo=None)


def _desde(db: Session, f: Fonte, ck: CheckpointCarga):
    """IN2: início da janela incremental da fonte = marca d'água - margem. Sem marca, inicializa pela bronze (fica fixa
    até o fim do ciclo, para o keyset não pular linha entre execuções parciais). None = lê a fonte inteira."""
    if not f.campo_data:
        return None
    if ck.marca_dagua is None:
        if ck.ultima_chave:  # leitura inteira já começada (sem marca): continua inteira até o fim do ciclo
            return None
        ck.marca_dagua = _maior_bronze(db, f)
        if ck.marca_dagua is None:
            return None
    return ck.marca_dagua - timedelta(hours=get_settings().carga_incremental_margem_h)


def _ler(leitor, f: Fonte, ultima, lote: int, desde):
    return leitor.ler(f, ultima, lote) if desde is None else leitor.ler(f, ultima, lote, desde=desde)


def completa(db: Session, leitor, cfg: ConfigGE, usuario: str | None = None, agora: datetime | None = None,
             modo: str = "completa") -> Execucao:
    agora = agora or datetime.now(UTC)
    if not amostra_ok(db):
        raise CargaRecusada(SEM_AMOSTRA, "sem_amostra")
    janela_ignorada = checa_janela(agora)
    trava = _trava(db)
    if trava is None or completa_em_andamento(db):
        if trava is not None:
            _solta(trava)
        raise CargaRecusada(EM_ANDAMENTO, "em_andamento")
    try:
        return _completa(db, leitor, cfg, usuario, agora, janela_ignorada, modo)
    finally:
        _solta(trava)


def incremental(db: Session, leitor, cfg: ConfigGE, usuario: str | None = None,
                agora: datetime | None = None) -> Execucao:
    """IN2/IN6: mesma trava, teto de lotes, pausa e cancelamento da completa; só lê o alterado desde a marca."""
    return completa(db, leitor, cfg, usuario, agora, modo="incremental")


def _completa(db: Session, leitor, cfg: ConfigGE, usuario, agora: datetime, janela_ignorada: bool,
              modo: str = "completa") -> Execucao:
    teto, lote, pausa = max(1, int(cfg.max_lotes)), int(cfg.lote), pausa_lote_ms()
    inc = modo == "incremental"
    try:
        ex = _nova_execucao(db, TIPO_COMPLETA, usuario, modo)
    except IntegrityError:  # índice único parcial (0025): outra completa entrou primeiro
        db.rollback()
        raise CargaRecusada(EM_ANDAMENTO, "em_andamento") from None
    if janela_ignorada:
        db.add(Log(execucao_id=ex.id, nivel="WARNING",
                   mensagem=("Janela noturna ignorada: SIGPMF_EDUCACAO_JANELA_NOTURNA=false"
                             f" (fora da janela: {'sim' if not dentro_da_janela(agora)[0] else 'não'}).")))
        db.commit()
    eid, lotes, cancelada = ex.id, 0, False
    status, mensagem, pendentes = "falha", None, None
    erro: dict = {}
    por_fonte: dict[str, dict] = {}
    try:
        # ciclo já começado por uma execução anterior (parcial): a bronze pode ter mudado lá, então a prata é refeita
        ciclo_anterior = any(c.concluida or c.ultima_chave for c in (_checkpoint(db, f) for f in FONTES))
        for f in FONTES:
            if cancelada:
                break
            ck = _checkpoint(db, f)
            desde = _desde(db, f, ck) if inc else None
            while not ck.concluida and lotes < teto and not cancelada:
                ultima = tuple(ck.ultima_chave) if ck.ultima_chave else None
                linhas = _ler(leitor, f, ultima, lote, desde)
                if linhas:
                    c = _ingere(db, f, linhas, ex)
                    _soma(ex, c)
                    pf = por_fonte.setdefault(f.nome, {"lidos": 0, "novos": 0, "atualizados": 0, "desde": desde})
                    for k in ("lidos", "novos", "atualizados"):
                        pf[k] += c.get(k, 0)
                    ck.ultima_chave = _chave(f, linha_json(linhas[-1]))
                    ck.posicao = (ck.posicao or 0) + len(linhas)
                ck.concluida = len(linhas) < lote
                ck.execucao_id, ck.atualizado_em = ex.id, datetime.now(UTC)
                ex.tabela_atual = f.nome
                lotes += 1
                ex.lotes = lotes
                ex.atualizado_em = datetime.now(UTC)  # batida por lote (órfã = sem batida há N min)
                db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=f"{f.sistema}.{f.nome}: lote {lotes} com {len(linhas)} linhas"))
                if ck.concluida and inc:  # IN8: "incremental desde <marca>" e linhas novas/alteradas por tabela
                    pf = por_fonte.get(f.nome, {"lidos": 0, "novos": 0, "atualizados": 0})
                    ini = f"desde {desde:%d/%m/%Y %H:%M}" if desde is not None else "leitura inteira (sem marca d'água)"
                    db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=(
                        f"{f.sistema}.{f.nome}: incremental {ini}; {pf['lidos']} lidas, {pf['novos']} novas, "
                        f"{pf['atualizados']} alteradas.")))
                if ck.concluida and not inc and not (ck.posicao or 0):
                    db.add(Log(execucao_id=ex.id, nivel="WARNING",
                               mensagem=f"[{tabelas_vazias.CODIGO}] {f.sistema}.{f.nome}: fonte concluída com 0 linhas lidas."))
                db.commit()  # lote + checkpoint + batida na mesma transação
                cancelada = _cancelamento_pedido(eid)  # para no fim deste lote
                if not cancelada and lotes < teto and not (ck.concluida and f is FONTES[-1]):
                    cancelada = _pausa(eid, pausa)
        cks = [_checkpoint(db, f) for f in FONTES]
        if cancelada:
            status = "cancelada"
            mensagem = f"Cancelada no fim do lote {lotes}; o já gravado fica e o checkpoint continua dali."
        elif inc and all(c.concluida for c in cks) and not ciclo_anterior and not (
                (db.get(Execucao, eid).novos or 0) + (db.get(Execucao, eid).atualizados or 0)):
            _fecha_ciclo(db, cks)
            status, r = "sucesso", None
            mensagem = "Concluída · nenhuma linha nova (incremental; prata e ouro não foram refeitos)."
        elif all(c.concluida for c in cks):
            ex = db.get(Execucao, eid)
            aviso = ""
            if not inc:  # incremental com 0 linhas numa fonte é o normal (nada mudou), não tabela vazia
                aviso = tabelas_vazias.registra(db, ex, SISTEMA, [c.tabela for c in cks if not (c.posicao or 0)])
            if aviso:
                erro = {"erro_classe": ex.erro_classe, "erro_tabela": ex.erro_tabela}
            db.commit()
            with _BatidaEmFundo(eid, BATIDA_PROCESSA_S):
                r = educacao.processa(db, agora)
            _fecha_ciclo(db, cks)
            status = "sucesso"
            mensagem = ("Incremental: " if inc else "") + (f"Ciclo completo: {r['alunos']} alunos ({r['vinculados']} vinculados, {r['revisao']} em revisão, "
                        f"{r['quarentena']} em quarentena, {r['excluidos']} excluídos, {r['inativos']} inativos), {r['matriculas']} matrículas.")
            pendentes = r["revisao"] + r["quarentena"]
            mensagem = corta(mensagem + aviso, 1000)
        else:
            status = "parcial"
            mensagem = f"Parada no teto de {teto} lote(s); a próxima execução continua do checkpoint."
    except TiposDocumentoAusentes as exc:  # motivo fixo (código + texto), nunca str(exc)
        db.rollback()
        status, mensagem = "falha", f"Carga falhou: {MSG_SEM_TIPO_DOCUMENTO} Checkpoint mantido no último lote gravado."
        erro = {"erro_classe": exc.codigo, "erro_tabela": "SYS_TipoDocumentacao"}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        status, mensagem = "falha", f"Carga falhou ({type(exc).__name__}); checkpoint mantido no último lote gravado."
        erro = {"erro_classe": None, "erro_tabela": None}
    # Finaliza só se ainda está em_andamento (não sobrescreve interrompida/falha marcada por outro caminho).
    vals = {"status": status, "mensagem": mensagem, "lotes": lotes, "finalizado_em": datetime.now(UTC),
            "atualizado_em": datetime.now(UTC)}
    if pendentes is not None:
        vals["pendentes"] = pendentes
    vals.update(erro)
    db.execute(update(Execucao).where(Execucao.id == eid, Execucao.status == "em_andamento").values(**vals))
    db.commit()
    ex = db.get(Execucao, eid)
    db.refresh(ex)
    return ex


def _fecha_ciclo(db: Session, cks: list[CheckpointCarga]) -> None:
    """Fim do ciclo: o keyset volta ao início, mas a marca d'água (IN1) avança para a maior data gravada na bronze."""
    por_nome = {f.nome: f for f in FONTES}
    for c in cks:
        f = por_nome.get(c.tabela)
        if f is not None and f.campo_data:
            maior = _maior_bronze(db, f)
            if maior is not None and (c.marca_dagua is None or maior > c.marca_dagua):
                c.marca_dagua = maior
        c.concluida, c.ultima_chave, c.posicao = False, None, 0


def prepara_releitura(db: Session, fonte: str, usuario: str | None) -> dict:
    """Prepara a releitura de UMA fonte da educação, sem carga completa das outras (auditado).

    Só mexe em controle.checkpoints_carga: a fonte pedida volta ao início (posição 0, sem chave, não concluída) e as
    outras ficam concluídas com a posição = linhas que já têm na bronze. A próxima carga completa da educação lê só
    essa fonte e, ao fechar o ciclo, reconstrói a prata a partir da bronze inteira. Nada é apagado da bronze."""
    from sqlalchemy import func

    from app.connectors.gestaoescolar.consultas import POR_NOME
    from app.core.audit import audita
    from app.models.camadas import RegistroBronze
    if fonte not in POR_NOME:
        raise CargaRecusada(f"Fonte desconhecida no conector de educação: {fonte}.", "fonte_desconhecida")
    if completa_em_andamento(db):
        raise CargaRecusada(EM_ANDAMENTO, "em_andamento")
    antes = {}
    for f in FONTES:
        ck = _checkpoint(db, f)
        antes[f.nome] = {"posicao": ck.posicao, "concluida": ck.concluida}
        if f.nome == fonte:
            ck.posicao, ck.ultima_chave, ck.concluida = 0, None, False
        else:
            ck.concluida = True
            ck.posicao = int(db.scalar(select(func.count(func.distinct(RegistroBronze.id_origem))).where(
                RegistroBronze.sistema_origem == f.sistema, RegistroBronze.tabela == f.nome)) or 0)
        ck.atualizado_em = datetime.now(UTC)
    db.commit()
    audita(db, usuario, "educacao_releitura_fonte", f"fonte:{fonte}", {"fonte": fonte, "checkpoints_antes": antes})
    return {"fonte": fonte, "outras_concluidas": len(FONTES) - 1}
