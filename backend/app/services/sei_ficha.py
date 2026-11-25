"""Bloco Processos SEI da ficha do cidadão e da empresa.

Lê só a bronze já gravada pela carga (processo público, interessado com peso 1.0).
Não chama o SEI. Sem CPF/CNPJ no interessado não há vínculo: o nome fica na fila de revisão.
Processo restrito ou sigiloso não está na bronze pública e não entra na lista nem na contagem.
"""
from datetime import datetime
from urllib.parse import urlencode

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.connectors.sei import carga
from app.connectors.sei.carta import rotulo_servico
from app.connectors.sei.concluido import status_processo
from app.connectors.sei.config import ConfigSei
from app.explorador.mascara import mascara_valor
from app.identity import documento as docmod
from app.identity.cpf import mascara as mascara_cpf
from app.identity.nome import mascara_nome
from app.util.datas import normaliza_data, normaliza_data_iso

PESQUISA_PUBLICA = "https://cidades.sei.sp.gov.br/franca/modulos/pesquisa/md_pesq_processo_pesquisar.php"
MSG_VAZIO = "Nenhum processo SEI"
MSG_FONTE = "Fonte SEI ainda não liberada"

_PROCESSOS = text("""
SELECT dados FROM bronze.registros
WHERE id IN (
    SELECT max(id) FROM bronze.registros
    WHERE sistema_origem = 'SEI' AND tabela = 'processos'
      AND quarentena IS NULL AND ambiente_origem = 'producao'
    GROUP BY id_origem)
  AND dados->>'nivel_acesso' = '0'
  AND EXISTS (
    SELECT 1 FROM jsonb_array_elements(
        CASE WHEN jsonb_typeof(dados->'interessados') = 'array'
             THEN dados->'interessados' ELSE '[]'::jsonb END) elem
    WHERE elem->>:campo = :valor)
""")

_LINHAS = text("""
SELECT dados FROM bronze.registros
WHERE id IN (
    SELECT max(id) FROM bronze.registros
    WHERE sistema_origem = 'SEI' AND tabela = :tab
      AND quarentena IS NULL AND ambiente_origem = 'producao'
    GROUP BY id_origem)
  AND dados->>:campo IN :ids
""").bindparams(bindparam("ids", expanding=True))


def fonte_liberada() -> bool:
    """A mesma flag da carga incremental. O default continua falso: a ficha não libera a fonte sozinha."""
    return ConfigSei.do_ambiente().carga_liberada


def link_pesquisa_publica(protocolo: str) -> str:
    """Pesquisa Pública do SEI Cidades de Franca. A query leva só o protocolo."""
    return PESQUISA_PUBLICA + "?" + urlencode({
        "acao_externa": "protocolo_pesquisar",
        "txtProtocoloPesquisa": protocolo,
    })


def _peso(v) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _parse(v) -> datetime | None:
    if not v:
        return None
    s = str(v).strip()
    for fmt, n in (("%d/%m/%Y %H:%M:%S", 19), ("%d/%m/%Y %H:%M", 16), ("%Y-%m-%dT%H:%M:%S", 19),
                   ("%Y-%m-%d %H:%M:%S", 19), ("%d/%m/%Y", 10), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(s[:n], fmt)
        except ValueError:
            continue
    d = normaliza_data(s)
    return datetime(d.year, d.month, d.day) if d else None


def _iso_hora(v) -> str | None:
    dt = _parse(v)
    if dt is None:
        return normaliza_data_iso(v)
    if dt.hour or dt.minute or dt.second:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    return dt.date().isoformat()


def _desc(v) -> str | None:
    if not v:
        return None
    return mascara_valor(None, str(v)[:300])


def _sigla(u) -> str | None:
    if isinstance(u, dict):
        s = u.get("sigla")
        return str(s).strip() or None if s else None
    return None


def _processos_ligados(db: Session, campo: str, valor: str) -> list[dict]:
    linhas = db.execute(_PROCESSOS, {"campo": campo, "valor": valor}).all()
    out = []
    for (dados,) in linhas:
        if not isinstance(dados, dict) or dados.get("nivel_acesso") != "0":
            continue
        if any(_peso(i.get("peso")) == 1.0 and i.get(campo) == valor
               for i in dados.get("interessados") or [] if isinstance(i, dict)):
            out.append(dados)
    return out


def _por_processo(db: Session, tabela: str, campo: str, ids: list[str]) -> dict[str, list[dict]]:
    if not ids:
        return {}
    grupos: dict[str, list[dict]] = {}
    for (dados,) in db.execute(_LINHAS, {"tab": tabela, "campo": campo, "ids": ids}):
        if not isinstance(dados, dict):
            continue
        chave = str(dados.get(campo) or "")
        grupos.setdefault(chave, []).append(dados)
    return grupos


def _ultimo(proc: dict, andamentos: list[dict]) -> dict | None:
    emb = proc.get("ultimo_andamento") if isinstance(proc.get("ultimo_andamento"), dict) else None
    melhor, quando = emb, _parse((emb or {}).get("data_hora"))
    for a in andamentos:
        if not isinstance(a, dict) or a.get("nivel_acesso") not in (None, "0"):
            continue
        dt = _parse(a.get("data_hora"))
        if melhor is None or (dt is not None and (quando is None or dt >= quando)):
            desc = a.get("descricao")
            if not desc and emb and _parse(emb.get("data_hora")) == dt:
                desc = emb.get("descricao")
            melhor = {"data_hora": a.get("data_hora"), "descricao": desc, "id_tarefa": a.get("id_tarefa"),
                      "unidade": a.get("unidade")}
            quando = dt
    return melhor


def _cartao(proc: dict, andamentos: list[dict], documentos: list[dict]) -> dict:
    tipo = proc.get("tipo") if isinstance(proc.get("tipo"), dict) else {}
    tipo_sei = tipo.get("nome")
    ult = _ultimo(proc, andamentos)
    desc_bruta = (ult or {}).get("descricao")
    tarefa = (ult or {}).get("id_tarefa")
    unidade = _sigla((ult or {}).get("unidade")) or proc.get("unidade_atual") or _sigla(proc.get("unidade_geradora"))
    n_docs = sum(1 for d in documentos if isinstance(d, dict) and str(d.get("nivel_acesso") or "0") == "0")
    protocolo = proc.get("protocolo") or ""
    rotulo = rotulo_servico(tipo_sei)
    return {"id_procedimento": proc.get("id_procedimento"),
            "protocolo": protocolo,
            "tipo_sei": tipo_sei,
            "servico_carta": rotulo if rotulo and rotulo != tipo_sei else None,
            "rotulo": rotulo or "Tipo não informado",
            "data_autuacao": normaliza_data_iso(proc.get("data_autuacao")),
            "unidade_atual": unidade,
            "ultimo_andamento": None if ult is None else {
                "data_hora": _iso_hora(ult.get("data_hora")), "descricao": _desc(desc_bruta)},
            "status": status_processo(str(tarefa) if tarefa else None, desc_bruta if isinstance(desc_bruta, str) else None),
            "documentos": n_docs,
            "link": link_pesquisa_publica(protocolo) if protocolo else None}


def _tem_outros(proc: dict, campo: str | None, valor: str | None) -> bool:
    for i in proc.get("interessados") or []:
        if not isinstance(i, dict):
            continue
        if campo and _peso(i.get("peso")) == 1.0 and i.get(campo) == valor:
            continue
        return True
    return False


def _ordena(itens: list[dict]) -> list[dict]:
    def chave(p: dict):
        aberto = 0 if p["status"] == "Em andamento" else 1
        dt = _parse((p.get("ultimo_andamento") or {}).get("data_hora"))
        marca = -(dt.timestamp()) if dt else 0
        return (aberto, marca, p.get("protocolo") or "")
    return sorted(itens, key=chave)


def listar(db: Session, *, campo: str, valor: str) -> dict:
    """campo é cidadao_id ou empresa_id (uuid). Não recebe documento."""
    if not fonte_liberada():
        return {"fonte_liberada": False, "mensagem": MSG_FONTE, "total": 0, "processos": []}
    procs = _processos_ligados(db, campo, valor)
    ids = [str(p.get("id_procedimento")) for p in procs if p.get("id_procedimento")]
    ands = _por_processo(db, carga.TAB_ANDAMENTOS, "id_processo", ids)
    docs = _por_processo(db, carga.TAB_DOCUMENTOS, "id_procedimento", ids)
    itens = []
    for p in procs:
        idp = str(p.get("id_procedimento") or "")
        cartao = _cartao(p, ands.get(idp, []), docs.get(idp, []))
        cartao["tem_outros"] = _tem_outros(p, campo, valor)
        itens.append(cartao)
    itens = _ordena(itens)
    return {"fonte_liberada": True, "mensagem": None if itens else MSG_VAZIO, "total": len(itens), "processos": itens}


def _outros(proc: dict, campo: str, valor: str) -> list[dict]:
    return [i for i in proc.get("interessados") or []
            if isinstance(i, dict) and not (_peso(i.get("peso")) == 1.0 and i.get(campo) == valor)]


def interessados_do_processo(db: Session, *, campo: str, valor: str, id_procedimento: str) -> list[dict] | None:
    if not fonte_liberada():
        return None
    for p in _processos_ligados(db, campo, valor):
        if str(p.get("id_procedimento") or "") == id_procedimento:
            return _outros(p, campo, valor)
    return None


def visao_outros(lista: list[dict], *, revela: bool) -> list[dict]:
    """Só nome e documento. Fora do revelar, os dois saem mascarados."""
    out = []
    for i in lista:
        cpf, cnpj, nome = i.get("cpf"), i.get("cnpj"), i.get("nome")
        if not revela:
            cpf = mascara_cpf(cpf) if cpf else None
            cnpj = docmod.mascara(cnpj) if cnpj else None
            nome = mascara_nome(nome)
        out.append({"nome": nome, "cpf": cpf, "cnpj": cnpj})
    return out
