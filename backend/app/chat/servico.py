"""Liga a pergunta ao catálogo: o modelo escolhe, o backend confere escopo, executa e só então pede o texto."""
import json
import logging

from sqlalchemy.orm import Session

from app.chat.catalogo import CONSULTAS, FONTES, ForaDoCatalogo, validar
from app.chat.catalogo import executar as executar_consulta
from app.chat.litellm import Indisponivel, ToolsRecusadas, completar
from app.core.audit import audita
from app.models.controle import Usuario
from app.paineis.rotas import escopo_do_usuario
from app.services.acesso_secretaria import dono_dominio

log = logging.getLogger("sigpmf.chat")

ACAO = "chat_consulta"
MSG_NEGADO = {
    "sem_vinculo": "Sem vínculo vigente com uma secretaria.",
    "outra_secretaria": "Sem acesso aos dados desta fonte.",
}
_TOOL = "consultar_dados"
TOOLS = [{
    "type": "function",
    "function": {
        "name": _TOOL,
        "description": "Escolhe uma consulta agregada do catálogo e a fonte.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "consulta": {"type": "string", "enum": sorted(CONSULTAS)},
                "parametros": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"fonte": {"type": "string", "enum": sorted(FONTES)}},
                    "required": ["fonte"],
                },
            },
            "required": ["consulta", "parametros"],
        },
    },
}]
_SISTEMA = (
    "Você escolhe uma consulta do catálogo do SIGPMF e nada além dela. "
    "ultima_carga: última carga de uma fonte. "
    "empresas_ultima_carga: quantidade de empresas na última carga; fonte só sil ou iss. "
    "registros_por_fonte: registros agregados (bronze, prata, ouro, pendentes) de uma fonte. "
    f"Fontes: {', '.join(sorted(FONTES))}. "
    f"Chame a função {_TOOL}. Se não puder usar funções, responda somente com um JSON "
    '{"consulta":"...","parametros":{"fonte":"..."}}. '
    'Se a pergunta não couber no catálogo, responda somente {"consulta": null, "parametros": {}}. '
    "Não peça nem repita dado pessoal."
)
_JSON = _SISTEMA + " Responda somente o JSON, sem texto antes ou depois."
_REDIGIR = (
    "Redija em português, em até três frases, usando somente o JSON recebido. "
    "Não invente número, data, nome, documento ou endereço. "
    "Se estado for sem_carga, diga que não há carga desta fonte. "
    "Cite a fonte, os filtros e a data da carga quando existirem."
)


def _msgs(pergunta: str, *, json_estrito: bool) -> list[dict]:
    return [{"role": "system", "content": _JSON if json_estrito else _SISTEMA},
            {"role": "user", "content": pergunta}]


def _mensagem(resp: dict) -> dict:
    try:
        msg = resp["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise Indisponivel("resposta") from None
    if not isinstance(msg, dict):
        raise Indisponivel("resposta")
    return msg


def _json_texto(texto) -> dict | None:
    if not isinstance(texto, str):
        return None
    t = texto.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        v = json.loads(t[i:j + 1])
    except json.JSONDecodeError:
        return None
    return v if isinstance(v, dict) else None


def _da_mensagem(msg: dict) -> dict | None:
    for chamada in msg.get("tool_calls") or []:
        fn = (chamada or {}).get("function") or {}
        if fn.get("name") != _TOOL:
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            args = _json_texto(args)
        if isinstance(args, dict):
            return args
    return _json_texto(msg.get("content"))


def escolher(pergunta: str) -> dict:
    """Tool calling; se o modelo recusar tools ou não devolver chamada, uma segunda ida pede JSON estrito."""
    try:
        resp = completar(_msgs(pergunta, json_estrito=False), TOOLS)
    except ToolsRecusadas:
        resp = None
    bruto = _da_mensagem(_mensagem(resp)) if resp is not None else None
    if bruto is None:
        bruto = _da_mensagem(_mensagem(completar(_msgs(pergunta, json_estrito=True), None)))
    if bruto is None:
        raise ForaDoCatalogo()
    return bruto


def _autorizada(db: Session, u: Usuario, fonte_id: str) -> tuple[bool, str]:
    """O mesmo recorte dos painéis: admin e vínculo TODAS veem tudo; os demais, só a secretaria dona do domínio."""
    esc = escopo_do_usuario(db, u)
    if esc is None:
        return True, ""
    if not esc:
        return False, "sem_vinculo"
    dono = dono_dominio(db, FONTES[fonte_id])
    if dono is None or dono not in esc:
        return False, "outra_secretaria"
    return True, ""


def _nome_curto(bruto) -> str | None:
    n = bruto.get("consulta") if isinstance(bruto, dict) else None
    if isinstance(n, str) and n.replace("_", "").isalnum() and 1 <= len(n) <= 40:
        return n
    return None


def _params_auditoria(bruto) -> dict:
    """Só a fonte, e só se for um texto curto. Qualquer outro parâmetro (pode ser dado pessoal) não entra."""
    p = bruto.get("parametros") if isinstance(bruto, dict) else None
    fonte = p.get("fonte") if isinstance(p, dict) else None
    if isinstance(fonte, str) and 1 <= len(fonte.strip()) <= 40:
        return {"fonte": " ".join(fonte.split())}
    return {}


def _audita(db: Session, u: Usuario, ip: str | None, consulta: str | None, params: dict,
            fonte: str | None, resultado: str, motivo: str | None = None) -> None:
    det = {"consulta": consulta, "parametros": params, "fonte": fonte, "resultado": resultado}
    if motivo:
        det["motivo"] = motivo
    audita(db, u.email, ACAO, f"chat:{consulta or '-'}", det, ip)


def _frase(ag: dict) -> str:
    if ag["estado"] == "sem_carga":
        return f"Não há carga registrada para {ag['fonte']}."
    nums = ag.get("numeros") or {}
    pedacos = ", ".join(f"{k} {v}" for k, v in nums.items()) or "sem números"
    quando = ag.get("carga_em") or "sem data de carga"
    return f"{ag['fonte']}: {pedacos}. Carga em {quando}. Filtro: fonte {ag['fonte']}."


def _limpa_texto(texto: str) -> str:
    t = " ".join((texto or "").split())
    if not t or "Traceback" in t:
        return ""
    return t[:1500]


def _redigir(ag: dict) -> str:
    resp = completar([
        {"role": "system", "content": _REDIGIR},
        {"role": "user", "content": json.dumps(ag, ensure_ascii=False)},
    ], None)
    return _limpa_texto(str(_mensagem(resp).get("content") or "")) or _frase(ag)


def _saida(ag, resposta: str) -> dict:
    return {"resposta": resposta, "estado": ag.estado, "consulta": ag.consulta, "fonte": ag.rotulo,
            "filtros": ag.filtros, "carga_em": ag.carga_em, "status_carga": ag.status_carga, "numeros": ag.numeros}


def responder(db: Session, u: Usuario, pergunta: str, ip: str | None) -> dict:
    bruto = None
    try:
        bruto = escolher(pergunta)
        consulta, params = validar(bruto)
    except Indisponivel:
        _audita(db, u, ip, _nome_curto(bruto) if bruto else None, {}, None, "erro_llm")
        raise
    except ForaDoCatalogo as exc:
        origem = exc.bruto if exc.bruto is not None else bruto
        _audita(db, u, ip, _nome_curto(origem), _params_auditoria(origem), None, "negado", "fora_catalogo")
        raise ForaDoCatalogo() from None
    fonte_id = params["fonte"]
    ok, motivo = _autorizada(db, u, fonte_id)
    if not ok:
        from app.chat.catalogo import _progresso
        try:
            rotulo = _progresso(fonte_id).nome
        except ForaDoCatalogo:
            rotulo = fonte_id
        _audita(db, u, ip, consulta, params, rotulo, "negado", motivo)
        return {"resposta": MSG_NEGADO[motivo], "estado": "negado", "consulta": consulta, "fonte": rotulo,
                "filtros": {"fonte": rotulo}, "carga_em": None, "status_carga": None, "numeros": None}
    ag = executar_consulta(db, consulta, params)
    payload = {"consulta": ag.consulta, "estado": ag.estado, "fonte": ag.rotulo, "filtros": ag.filtros,
               "carga_em": ag.carga_em, "status_carga": ag.status_carga, "numeros": ag.numeros}
    try:
        texto = _redigir(payload)
    except Indisponivel:
        _audita(db, u, ip, consulta, params, ag.rotulo, "erro_llm")
        raise
    _audita(db, u, ip, consulta, params, ag.rotulo, ag.estado)
    log.info("chat %s fonte=%s resultado=%s", consulta, fonte_id, ag.estado)
    return _saida(ag, texto)
