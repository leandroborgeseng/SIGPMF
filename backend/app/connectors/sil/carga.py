"""Enriquecimento por chave na bronze (SIL4/SIL5): consulta um lote de chaves num endpoint e grava a resposta bruta.

- Uma linha de bronze por chave consultada (sistema "SIL", tabela do endpoint). O id_origem é um hash da chave
  (nada de CPF/CNPJ/inscrição em id, log ou mensagem); a chave e a resposta ficam no JSON `dados` (bronze bruta).
- Contagem: lidos = localizados + nao_localizados (404) + erros. Para o lote depois de N falhas seguidas.
- Nenhuma chamada se o conector estiver desligado (SilDesabilitado)."""
import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.bronze.ingestao import ingere
from app.connectors.sil.cliente import ClienteSil, ErroSil, FalhaCredencial
from app.connectors.sil.config import ConfigSil
from app.connectors.sil.endpoints import EndpointSil
from app.models.controle import Execucao, Log

SISTEMA = "SIL"
TIPO_EXECUCAO = "enriquecimento_sil"
LIMITE_LOTE = 1000  # SIL4: amostra de até 1000 chaves


def id_consulta(endpoint: EndpointSil, chave: str) -> str:
    return f"{endpoint.nome}:{hashlib.sha256(f'{endpoint.nome}|{chave}'.encode()).hexdigest()[:40]}"


def consultar_lote(cliente: ClienteSil, endpoint: EndpointSil, chaves: Iterable[str],
                   falhas_seguidas_max: int | None = None) -> dict:
    """Consulta cada chave (dígitos/texto como veio). Devolve registros para a bronze e contagens."""
    limite = falhas_seguidas_max or cliente.config.falhas_seguidas_max
    regs, cont = [], {"lidos": 0, "localizados": 0, "nao_localizados": 0, "erros": 0, "parou": False, "motivo": None}
    seguidas = 0
    for chave in chaves:
        chave = str(chave).strip()
        if not chave:
            continue
        cont["lidos"] += 1
        try:
            resp = cliente.get(endpoint.caminho, {endpoint.parametro: chave, **dict(endpoint.fixos)})
        except FalhaCredencial as exc:
            cont["erros"] += 1
            cont["parou"], cont["motivo"] = True, str(exc)
            break
        except ErroSil as exc:
            cont["erros"] += 1
            seguidas += 1
            if seguidas >= limite:
                cont["parou"], cont["motivo"] = True, f"{limite} falhas seguidas ({exc})"
                break
            continue
        seguidas = 0
        if resp is None:
            cont["nao_localizados"] += 1
            continue
        cont["localizados"] += 1
        regs.append({"_id_sigpmf": id_consulta(endpoint, chave), "endpoint": endpoint.nome,
                     "tipo_chave": endpoint.tipo_chave, "chave_documento": chave, "resposta": resp})
    assert cont["lidos"] == cont["localizados"] + cont["nao_localizados"] + cont["erros"]
    return {"registros": regs, "contagem": cont}


def _lotes_configurados() -> int:
    """SIL11 / TL: por padrão UM lote do consultarlote. Mais lotes só com SIL_CONSULTARLOTE_MAX_LOTES explícito."""
    import os
    try:
        return max(1, int(os.environ.get("SIL_CONSULTARLOTE_MAX_LOTES") or 1))
    except ValueError:
        return 1


def _itens_lote(resp) -> list[dict]:
    """data.empresas[].empresa (formato da doc); aceita também a lista direta de empresas."""
    if not isinstance(resp, dict):
        return []
    lista = (resp.get("data") or {}).get("empresas") if isinstance(resp.get("data"), dict) else None
    if lista is None:
        lista = resp.get("empresas") or []
    return [x.get("empresa", x) for x in lista if isinstance(x, dict)]


def paginar_lote(cliente: ClienteSil, endpoint: EndpointSil, max_lotes: int | None = None,
                 limite: int = LIMITE_LOTE, sequencia_inicial: int = 0) -> dict:
    """SIL11: Sequencia começa em 0 (Status=Ativo fixo); a próxima é o maior ID recebido; para em lote vazio, 404,
    ID que não avança, `max_lotes` (padrão 1) ou `limite` (teto 1000 na amostra). Nunca repete ID."""
    if not endpoint.paginado or not endpoint.campo_id:
        raise ValueError(f"endpoint {endpoint.nome} não é paginado")
    max_lotes = max_lotes or _lotes_configurados()
    limite = min(limite, LIMITE_LOTE)
    seq, vistos, regs = sequencia_inicial, set(), []
    cont = {"lidos": 0, "localizados": 0, "nao_localizados": 0, "erros": 0, "repetidos": 0, "sem_id": 0,
            "lotes": 0, "parou": False, "motivo": None, "fim": None, "proxima_sequencia": None}
    while cont["lotes"] < max_lotes and len(regs) < limite:
        try:
            resp = cliente.get(endpoint.caminho, {endpoint.parametro: str(seq), **dict(endpoint.fixos)})
        except ErroSil as exc:
            cont["erros"] += 1
            cont["parou"], cont["motivo"] = True, str(exc)
            break
        cont["lotes"] += 1
        itens = _itens_lote(resp) if resp is not None else []
        if not itens:
            cont["fim"] = "404" if resp is None else "lote_vazio"
            break
        maior = seq
        for it in itens:
            cont["lidos"] += 1
            try:
                ident = int(str(it.get(endpoint.campo_id)).strip())
            except (TypeError, ValueError):
                cont["sem_id"] += 1
                continue
            maior = max(maior, ident)
            if ident in vistos:
                cont["repetidos"] += 1
                continue
            if len(regs) >= limite:
                continue
            vistos.add(ident)
            cont["localizados"] += 1
            regs.append({"_id_sigpmf": id_consulta(endpoint, str(ident)), "endpoint": endpoint.nome,
                         "tipo_chave": endpoint.campo_id, "id_lote": ident, "resposta": it})
        if maior <= seq:  # o ID não avançou: parar em vez de pedir o mesmo lote de novo
            cont["fim"] = "sem_avanco"
            break
        seq = maior
        cont["proxima_sequencia"] = seq
    if cont["fim"] is None and not cont["parou"]:
        cont["fim"] = "limite" if len(regs) >= limite else "max_lotes"
    return {"registros": regs, "contagem": cont}


def executar_enriquecimento(db: Session, usuario: str, endpoint: EndpointSil, chaves: list[str] | None = None,
                            cliente: ClienteSil | None = None, config: ConfigSil | None = None,
                            execucao_id: int | None = None, max_lotes: int | None = None) -> Execucao:
    """Lote de até LIMITE_LOTE chaves -> bronze (transação única), controle com contagens. Só GET."""
    cfg = config or ConfigSil.do_ambiente()
    if cliente is None and not cfg.habilitado:
        from app.connectors.sil.cliente import SilDesabilitado
        raise SilDesabilitado("Conector SIL desligado: defina SIL_BASE_URL, SIL_CLIENT_ID e SIL_CLIENT_SECRET.")
    chaves = chaves or []
    if len(chaves) > LIMITE_LOTE:
        raise ValueError(f"lote acima de {LIMITE_LOTE} chaves (SIL4)")
    ex = db.get(Execucao, execucao_id) if execucao_id is not None else None
    if ex is None:  # execucao_id: já criada pela rota assíncrona (app.services.amostra_sil.iniciar_amostra)
        ex = Execucao(tipo=TIPO_EXECUCAO, sistema=SISTEMA, status="em_andamento", usuario=usuario,
                      atualizado_em=datetime.now(UTC))
        db.add(ex)
        db.commit()
    proprio = cliente is None
    cliente = cliente or ClienteSil(cfg)
    try:
        r = (paginar_lote(cliente, endpoint, max_lotes=max_lotes) if endpoint.paginado
             else consultar_lote(cliente, endpoint, chaves))
        c = r["contagem"]
        if r["registros"]:
            b = ingere(db, SISTEMA, endpoint.tabela_bronze, "_id_sigpmf", None, r["registros"], ex.id,
                       confirmar=False)
            ex.novos, ex.atualizados, ex.ignorados = b["novos"], b["atualizados"], b["ignorados"]
        ex.lidos, ex.lotes = c["lidos"], c.get("lotes", 1)
        ex.status = "falha" if c["parou"] else ("sucesso" if r["registros"] else "sem_novidades")
        ex.mensagem = (f"ambiente={cfg.ambiente or '-'} host={cfg.host or '-'} endpoint={endpoint.nome} lidos={c['lidos']} localizados={c['localizados']} "
                       f"nao_localizados={c['nao_localizados']} erros={c['erros']}"
                       + (f" lotes={c['lotes']} repetidos={c['repetidos']} fim={c['fim']} "
                          f"proxima_sequencia={c['proxima_sequencia']}" if endpoint.paginado else "")
                       + (f"; lote parado: {c['motivo']}" if c["parou"] else ""))
        ex.finalizado_em = datetime.now(UTC)
        db.add(Log(execucao_id=ex.id, nivel="ERROR" if c["parou"] else "INFO", mensagem=ex.mensagem))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        ex = db.get(Execucao, ex.id)
        ex.status, ex.finalizado_em = "falha", datetime.now(UTC)
        ex.mensagem = f"Falha no enriquecimento SIL: {exc if isinstance(exc, ErroSil) else type(exc).__name__}"
        db.add(Log(execucao_id=ex.id, nivel="ERROR", mensagem=ex.mensagem))
        db.commit()
    finally:
        if proprio:
            cliente.fechar()
    return ex
