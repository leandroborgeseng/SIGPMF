"""Liga a pergunta ao catálogo: o modelo escolhe, o backend confere escopo, executa e só então pede o texto."""
import json
import logging
import re

from sqlalchemy.orm import Session

from app.chat.catalogo import (
    CONSULTAS,
    ENTIDADES,
    FOLHA_POR_CODIGO,
    FONTES,
    ConsultaEstruturada,
    ForaDoCatalogo,
    consulta_nominal,
    executar_estruturada,
    resumo_para_modelo,
    secretaria_fora_do_escopo,
    validar,
    validar_estruturada,
)
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
    "sem_folha": "Sem acesso à folha desta secretaria. Nome e valor individual só aparecem para quem já vê a folha.",
}
_TOOL = "consultar_dados"
_ENTIDADE = "consultar_entidade"
_FUNCOES = {_TOOL, _ENTIDADE}
_CPF_FMT = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_CPF_DIG = re.compile(r"\b\d{11}\b")
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": _TOOL,
            "description": "Escolhe uma consulta agregada de carga e a fonte.",
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
    },
    {
        "type": "function",
        "function": {
            "name": _ENTIDADE,
            "description": "Consulta estruturada só de leitura. Não escreva SQL.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entidade": {"type": "string", "enum": sorted(ENTIDADES)},
                    "filtros": {"type": "array"},
                    "agrupamento": {"type": "array"},
                    "metrica": {"type": "object"},
                    "ordenacao": {"type": "array"},
                    "limite": {"type": "integer"},
                },
                "required": ["entidade"],
            },
        },
    },
]
_SISTEMA = (
    "Você consulta o SIGPMF só pelo catálogo. Não escreva SQL. Não peça nem repita CPF. "
    "Cargas: chame consultar_dados. ultima_carga, empresas_ultima_carga (fonte só sil ou iss) "
    "ou registros_por_fonte. "
    f"Fontes: {', '.join(sorted(FONTES))}. "
    "Folha, empresas ou dívida: chame consultar_entidade. JSON com entidade, filtros "
    "(campo, operador eq|ne|gt|gte|lt|lte, valor), agrupamento, metrica "
    "(funcao max|min|soma|media|contagem e campo), ordenacao (campo, direcao asc|desc) e limite. "
    "Maior ou menor valor de uma pessoa: metrica max ou min, sem agrupamento. "
    + resumo_para_modelo()
    + ' Se não couber no catálogo, responda somente {"consulta": null, "parametros": {}}. '
)
_JSON = _SISTEMA + " Responda somente o JSON, sem texto antes ou depois."
_REDIGIR = (
    "Redija em português, em até três frases, usando somente o JSON recebido. "
    "Responda só com texto, sem JSON. "
    "Não invente número, data ou nome. Não escreva CPF. "
    "Se estado for sem_carga, diga que não há carga desta fonte. "
    "Se houver nome e um valor, cite os dois. "
    "Não repita a data da carga: a tela mostra fonte, filtros e data."
)


def _sem_cpf(texto: str) -> str:
    return _CPF_DIG.sub("[removido]", _CPF_FMT.sub("[removido]", texto or ""))


def extrair_texto_resposta(texto: str) -> str:
    """O modelo às vezes devolve `{"resposta": "..."}` puro ou dentro de ```json. A API fica só com o texto."""
    bruto = (texto or "").strip()
    if not bruto or "Traceback" in bruto:
        return ""
    candidato = _sem_cerca(bruto)
    obj = _json_objeto(candidato)
    if isinstance(obj, dict):
        resp = obj.get("resposta")
        if not isinstance(resp, str):
            return ""
        if resp.strip().startswith("{") or resp.strip().startswith("```"):
            interno = extrair_texto_resposta(resp)
            if interno:
                resp = interno
        bruto = resp
    else:
        bruto = candidato
    return _sem_cpf(" ".join(bruto.split()))[:1500]


def _sem_cerca(t: str) -> str:
    s = t.strip()
    if not s.startswith("```"):
        return s
    linhas = s.splitlines()
    if linhas and linhas[0].startswith("```"):
        linhas = linhas[1:]
    if linhas and linhas[-1].strip().startswith("```"):
        linhas = linhas[:-1]
    return "\n".join(linhas).strip()


def _json_objeto(t: str) -> dict | None:
    t = t.strip()
    try:
        v = json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i < 0 or j <= i:
            return None
        try:
            v = json.loads(t[i:j + 1])
        except json.JSONDecodeError:
            return None
    return v if isinstance(v, dict) else None


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
        if fn.get("name") not in _FUNCOES:
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


def _folha_permissao(db: Session, u: Usuario) -> tuple[str, list[str] | None]:
    """A mesma função das telas (`escopo_do_usuario`): None = todas; lista = secretarias da folha; motivo = recusa."""
    esc = escopo_do_usuario(db, u)
    if esc is None:
        return "", None
    if not esc:
        return "sem_vinculo", None
    nomes = [FOLHA_POR_CODIGO[c] for c in sorted(esc) if c in FOLHA_POR_CODIGO]
    if not nomes:
        return "sem_folha", None
    return "", nomes


def _nome_curto(bruto) -> str | None:
    if not isinstance(bruto, dict):
        return None
    n = bruto.get("entidade") or bruto.get("consulta")
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


def _token(v, n=40) -> str | None:
    if not isinstance(v, str):
        return None
    t = " ".join(v.split())
    if 1 <= len(t) <= n and all(ch.isalnum() or ch in "_-" for ch in t):
        return t
    return None


def _resumo_recusa(bruto) -> dict | None:
    if not isinstance(bruto, dict) or "entidade" not in bruto:
        return None
    out = {}
    ent = _token(bruto.get("entidade"))
    if ent:
        out["entidade"] = ent
    filtros = bruto.get("filtros")
    if isinstance(filtros, list):
        campos = []
        for item in filtros:
            if isinstance(item, dict):
                campo = _token(item.get("campo"))
                if campo:
                    campos.append(campo)
        if campos:
            out["campos"] = campos[:8]
    return out or None


def _valor_aud(v):
    if isinstance(v, str):
        return _sem_cpf(v)[:80]
    if isinstance(v, (int, float, bool)):
        return v
    return _sem_cpf(str(v))[:40]


def _consulta_aud(c: ConsultaEstruturada) -> dict:
    return {
        "entidade": c.entidade,
        "filtros": [{"campo": f.campo, "operador": f.operador, "valor": _valor_aud(f.valor)} for f in c.filtros],
        "agrupamento": list(c.agrupamento),
        "metrica": {"funcao": c.metrica.funcao, "campo": c.metrica.campo} if c.metrica else None,
        "ordenacao": [{"campo": o.campo, "direcao": o.direcao} for o in c.ordenacao],
        "limite": c.limite,
    }


def _pergunta_aud(pergunta: str) -> str:
    return _sem_cpf(" ".join((pergunta or "").split()))[:500]


def _audita(db: Session, u: Usuario, ip: str | None, consulta: str | None, params: dict,
            fonte: str | None, resultado: str, pergunta: str, motivo: str | None = None,
            estruturada: dict | None = None, resumo: dict | None = None) -> None:
    det = {"consulta": consulta, "parametros": params, "fonte": fonte, "resultado": resultado,
           "pergunta": _pergunta_aud(pergunta)}
    if motivo:
        det["motivo"] = motivo
    if estruturada:
        det["consulta_estruturada"] = estruturada
    if resumo:
        det["resumo"] = resumo
    audita(db, u.email, ACAO, f"chat:{consulta or '-'}", det, ip)


def _frase(ag: dict) -> str:
    if ag["estado"] == "sem_carga":
        return f"Não há carga registrada para {ag['fonte']}."
    nums = ag.get("numeros") or {}
    pedacos = ", ".join(f"{k} {v}" for k, v in nums.items()) or "sem números"
    quando = ag.get("carga_em") or "sem data de carga"
    return f"{ag['fonte']}: {pedacos}. Carga em {quando}. Filtro: fonte {ag['fonte']}."


def _linhas_publicas(linhas: list[dict]) -> list[dict]:
    out = []
    for linha in linhas:
        out.append({k: _sem_cpf(v) if isinstance(v, str) else v for k, v in linha.items()})
    return out


def _frase_linhas(fonte: str, estado: str, linhas: list[dict]) -> str:
    if estado == "sem_carga":
        return f"Não há carga registrada para {fonte}."
    if not linhas:
        return f"Não há registros de {fonte} com esses filtros."
    if len(linhas) == 1:
        pedacos = ", ".join(f"{k} {v}" for k, v in linhas[0].items())
        return f"{pedacos}."
    return f"{len(linhas)} linhas no recorte de {fonte}."


def _redigir(ag: dict, fallback: str | None = None) -> str:
    resp = completar([
        {"role": "system", "content": _REDIGIR},
        {"role": "user", "content": json.dumps(ag, ensure_ascii=False)},
    ], None)
    return extrair_texto_resposta(str(_mensagem(resp).get("content") or "")) or fallback or _frase(ag)


def _saida(ag, resposta: str) -> dict:
    return {"resposta": resposta, "estado": ag.estado, "consulta": ag.consulta, "fonte": ag.rotulo,
            "filtros": ag.filtros, "carga_em": ag.carga_em, "status_carga": ag.status_carga, "numeros": ag.numeros}


def _negado(consulta: str, fonte: str, motivo: str) -> dict:
    return {"resposta": MSG_NEGADO[motivo], "estado": "negado", "consulta": consulta, "fonte": fonte,
            "filtros": {}, "carga_em": None, "status_carga": None, "numeros": None}


def _responder_entidade(db: Session, u: Usuario, pergunta: str, ip: str | None, bruto: dict) -> dict:
    try:
        consulta = validar_estruturada(bruto)
    except ForaDoCatalogo as exc:
        origem = exc.bruto if exc.bruto is not None else bruto
        _audita(db, u, ip, _nome_curto(origem), _params_auditoria(origem), None, "negado", pergunta,
                "fora_catalogo", _resumo_recusa(origem))
        raise ForaDoCatalogo() from None
    ent = ENTIDADES[consulta.entidade]
    estruturada = _consulta_aud(consulta)
    if ent.folha:
        motivo, nomes = _folha_permissao(db, u)
        if not motivo and secretaria_fora_do_escopo(consulta, nomes):
            motivo = "sem_folha"
        if motivo:
            _audita(db, u, ip, consulta.entidade, _params_auditoria(bruto), ent.rotulo, "negado", pergunta,
                    motivo, estruturada, {"nominal": consulta_nominal(consulta)})
            return _negado(consulta.entidade, ent.rotulo, motivo)
    else:
        ok, motivo = _autorizada(db, u, ent.fonte_id)
        if not ok:
            _audita(db, u, ip, consulta.entidade, _params_auditoria(bruto), ent.rotulo, "negado", pergunta,
                    motivo, estruturada)
            return _negado(consulta.entidade, ent.rotulo, motivo)
        nomes = None
    res = executar_estruturada(db, consulta, secretarias=nomes)
    linhas = _linhas_publicas(res.linhas)
    payload = {"estado": res.estado, "entidade": res.consulta, "filtros": res.filtros, "linhas": linhas}
    if res.metrica:
        payload["metrica"] = res.metrica
    try:
        texto = _redigir(payload, _frase_linhas(res.fonte, res.estado, linhas))
    except Indisponivel:
        _audita(db, u, ip, consulta.entidade, _params_auditoria(bruto), ent.rotulo, "erro_llm", pergunta,
                estruturada=estruturada)
        raise
    _audita(db, u, ip, consulta.entidade, _params_auditoria(bruto), ent.rotulo, res.estado, pergunta,
            estruturada=estruturada, resumo={"linhas": len(linhas), "nominal": res.nominal, "metrica": res.metrica})
    log.info("chat %s resultado=%s linhas=%s", consulta.entidade, res.estado, len(linhas))
    return {"resposta": texto, "estado": res.estado, "consulta": res.consulta, "fonte": res.fonte,
            "filtros": res.filtros, "carga_em": res.carga_em, "status_carga": res.status_carga, "numeros": res.numeros}


def responder(db: Session, u: Usuario, pergunta: str, ip: str | None) -> dict:
    bruto = None
    try:
        bruto = escolher(pergunta)
    except Indisponivel:
        _audita(db, u, ip, None, {}, None, "erro_llm", pergunta)
        raise
    except ForaDoCatalogo:
        _audita(db, u, ip, None, {}, None, "negado", pergunta, "fora_catalogo")
        raise ForaDoCatalogo() from None
    if isinstance(bruto, dict) and bruto.get("entidade"):
        return _responder_entidade(db, u, pergunta, ip, bruto)
    try:
        consulta, params = validar(bruto)
    except ForaDoCatalogo as exc:
        origem = exc.bruto if exc.bruto is not None else bruto
        _audita(db, u, ip, _nome_curto(origem), _params_auditoria(origem), None, "negado", pergunta, "fora_catalogo")
        raise ForaDoCatalogo() from None
    fonte_id = params["fonte"]
    ok, motivo = _autorizada(db, u, fonte_id)
    if not ok:
        from app.chat.catalogo import _progresso
        try:
            rotulo = _progresso(fonte_id).nome
        except ForaDoCatalogo:
            rotulo = fonte_id
        _audita(db, u, ip, consulta, params, rotulo, "negado", pergunta, motivo)
        return {"resposta": MSG_NEGADO[motivo], "estado": "negado", "consulta": consulta, "fonte": rotulo,
                "filtros": {"fonte": rotulo}, "carga_em": None, "status_carga": None, "numeros": None}
    ag = executar_consulta(db, consulta, params)
    payload = {"consulta": ag.consulta, "estado": ag.estado, "fonte": ag.rotulo, "filtros": ag.filtros,
               "carga_em": ag.carga_em, "status_carga": ag.status_carga, "numeros": ag.numeros}
    try:
        texto = _redigir(payload)
    except Indisponivel:
        _audita(db, u, ip, consulta, params, ag.rotulo, "erro_llm", pergunta)
        raise
    _audita(db, u, ip, consulta, params, ag.rotulo, ag.estado, pergunta)
    log.info("chat %s fonte=%s resultado=%s", consulta, fonte_id, ag.estado)
    return _saida(ag, texto)
