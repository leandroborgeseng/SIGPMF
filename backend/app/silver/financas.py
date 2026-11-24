"""Prata de finanças públicas pelo AUDESP (AU3): linha do arquivo do TCE -> silver.despesa / receita / restos_a_pagar.

- Valor no padrão brasileiro ("4144,00", "1.234,56") -> Decimal exato; anulação de empenho entra com sinal negativo.
- "código - descrição" separados (aplicação, elemento, natureza da receita...); a natureza da despesa sai dos 2 primeiros
  dígitos do elemento (31 pessoal, 32 juros, 33 custeio, 44 investimento, 45 inversão, 46 amortização).
- Credor: PJ com CNPJ normalizado (CN1, aceita o alfanumérico); PF só com o código do TCE (o arquivo de despesas não traz
  CPF; o de restos traz e o conector descarta antes da bronze); "IDENTIFICAÇÃO ESPECIAL" vira `especial`.
- `troca_ano` apaga as linhas daquele (tipo, ano do arquivo) e grava as do arquivo novo, na mesma transação: a prata é
  a foto do último arquivo publicado (o TCE corrige e republica).
"""
import re
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.identity import documento as docmod
from app.models.financas import Despesa, Receita, RestoAPagar

FASES = {"empenhado": "empenhado", "valor liquidado": "liquidado", "valor pago": "pago", "anulação": "anulado",
         "anulacao": "anulado"}
NATUREZA = {"31": "pessoal", "32": "juros", "33": "custeio", "44": "investimento", "45": "inversao", "46": "amortizacao"}
_COD = re.compile(r"^\s*([0-9A-Za-z.]+)\s*-\s*(.*)$")
_DOC_CNPJ = re.compile(r"CNPJ\D*([0-9A-Za-z./-]{14,18})", re.IGNORECASE)
_CODIGO_FINAL = re.compile(r"-\s*([0-9]+)\s*$")
MODELOS = {"despesas": Despesa, "receitas": Receita, "restos_a_pagar": RestoAPagar}


class LinhaInvalida(ValueError):
    """Linha do AUDESP fora do formato (valor, mês, fase). A mensagem não traz conteúdo da linha."""


def valor(v: str | None) -> Decimal:
    s = (v or "").strip()
    if not s:
        return Decimal("0")
    try:
        return Decimal(s.replace(".", "").replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise LinhaInvalida("valor fora do padrão brasileiro") from exc


def inteiro(v: str | None) -> int | None:
    s = (v or "").strip()
    return int(s) if s.isdigit() else None


def data_br(v: str | None) -> date | None:
    s = (v or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def codigo(v: str | None, tamanho: int = 8) -> tuple[str | None, str | None]:
    """'0310 - SAÚDE - GERAL' -> ('0310', 'SAÚDE - GERAL'); sem código -> (None, texto)."""
    s = (v or "").strip()
    if not s:
        return None, None
    m = _COD.match(s)
    if not m:
        return None, s
    return m.group(1).replace(".", "")[:tamanho], m.group(2).strip() or None


def natureza_do_elemento(cod: str | None) -> str | None:
    return NATUREZA.get((cod or "")[:2])


def credor(identificador: str | None) -> tuple[str | None, str | None, str | None]:
    """(tipo, cnpj, código do TCE). Nunca devolve CPF."""
    s = (identificador or "").strip()
    u = s.upper()
    if u.startswith("CNPJ"):
        m = _DOC_CNPJ.search(s)
        c = docmod.normaliza(m.group(1)) if m else None
        return "PJ", (c if c and docmod.cnpj_valido(c) else None), None
    if u.startswith("PESSOA F") or u.startswith("CPF"):
        m = _CODIGO_FINAL.search(s)
        return "PF", None, (m.group(1) if m and u.startswith("PESSOA F") else None)
    if u.startswith("IDENTIFICA"):
        m = _CODIGO_FINAL.search(s)
        return "especial", None, m.group(1) if m else None
    return None, None, None


def _txt(v: str | None, n: int) -> str | None:
    s = (v or "").strip()
    return s[:n] or None


def despesa(r: dict, ano_arquivo: int) -> dict:
    fase = FASES.get((r.get("tp_despesa") or "").strip().lower())
    ano, mes = inteiro(r.get("ano_exercicio")), inteiro(r.get("mes_referencia"))
    if fase is None or ano is None or mes is None or not 1 <= mes <= 13:
        raise LinhaInvalida("despesa sem fase, ano ou mês válidos")
    v = valor(r.get("vl_despesa"))
    tipo, cnpj, cod = credor(r.get("identificador_despesa"))
    apl_cod, apl = codigo(r.get("ds_cd_aplicacao_fixo"), 4)
    el_cod, el = codigo(r.get("ds_elemento"))
    return {"ano_arquivo": ano_arquivo, "id_origem": r["id_despesa_detalhe"], "ano": ano, "mes": mes,
            "orgao": _txt(r.get("ds_orgao"), 200), "fase": fase, "nr_empenho": _txt(r.get("nr_empenho"), 30),
            "dt_emissao": data_br(r.get("dt_emissao_despesa")), "credor_tipo": tipo, "credor_cnpj": cnpj,
            "credor_codigo_tce": cod, "credor_nome": _txt(r.get("ds_despesa"), 300),
            "funcao": _txt(r.get("ds_funcao_governo"), 120), "subfuncao": _txt(r.get("ds_subfuncao_governo"), 160),
            "cd_programa": _txt(r.get("cd_programa"), 10), "programa": _txt(r.get("ds_programa"), 300),
            "cd_acao": _txt(r.get("cd_acao"), 10), "acao": _txt(r.get("ds_acao"), 300),
            "fonte_recurso": _txt(r.get("ds_fonte_recurso"), 160), "aplicacao_codigo": apl_cod, "aplicacao": _txt(apl, 300),
            "modalidade_licitacao": _txt(r.get("ds_modalidade_lic"), 60), "elemento_codigo": el_cod,
            "elemento": _txt(el, 300), "natureza": natureza_do_elemento(el_cod), "valor": -abs(v) if fase == "anulado" else v}


def receita(r: dict, ano_arquivo: int) -> dict:
    ano, mes = inteiro(r.get("ano_exercicio")), inteiro(r.get("mes_referencia"))
    if ano is None or mes is None or not 1 <= mes <= 13:
        raise LinhaInvalida("receita sem ano ou mês válidos")
    out = {"ano_arquivo": ano_arquivo, "id_origem": r["id_rec_arrec_detalhe"], "ano": ano, "mes": mes,
           "orgao": _txt(r.get("ds_orgao"), 200), "poder": _txt(r.get("ds_poder"), 30),
           "fonte_recurso": _txt(r.get("ds_fonte_recurso"), 160), "aplicacao_variavel": _txt(r.get("ds_cd_aplicacao_variavel"), 300),
           "valor": valor(r.get("vl_arrecadacao"))}
    out["aplicacao_codigo"], apl = codigo(r.get("ds_cd_aplicacao_fixo"), 4)
    out["aplicacao"] = _txt(apl, 300)
    for campo, col in (("categoria", "ds_categoria"), ("subcategoria", "ds_subcategoria"), ("origem", "ds_fonte"),
                       ("d1", "ds_d1"), ("d2", "ds_dd2"), ("d3", "ds_d3")):
        cod, desc = codigo(r.get(col))
        out[f"{campo}_codigo"], out[campo] = cod, _txt(desc, 200)
    out["natureza_codigo"], nat = codigo(r.get("ds_tipo"))
    out["natureza"] = _txt(nat, 300)
    return out


_RP_VALORES = {"inscrito_proc": "Vl. Inscrito - RP Proc", "saldo_inicial_proc": "Vl. Saldo Inicial - RP Proc",
               "cancelado_proc": "Vl. Cancelado - RP Proc", "pago_proc": "Vl. Pago - RP Proc",
               "estorno_pago_proc": "Vl. Estorno Pago - RP Proc", "inscrito_nproc": "Vl. Inscrito - RP Não Proc",
               "saldo_inicial_nproc": "Vl. Saldo Inicial - RP Não Proc", "liquidado_nproc": "Vl. Liquidado - RP Não Proc",
               "estorno_liquidado_nproc": "Vl. Estorno Liquidado - RP Não Proc", "cancelado_nproc": "Vl. Cancelado - RP Não Proc",
               "pago_nproc": "Vl. Pago - RP Não Proc", "estorno_pago_nproc": "Vl. Estorno Pago - RP Não Proc"}


def resto(r: dict, ano_arquivo: int) -> dict:
    ano, mes = inteiro(r.get("Ano Balancete")), inteiro(r.get("Mes Balancete"))
    if ano is None or mes is None or not 1 <= mes <= 13:
        raise LinhaInvalida("restos a pagar sem ano ou mês do balancete válidos")
    ident = (r.get("ID Credor") or "").strip()
    tipo, cnpj, _ = credor(ident.replace(":", " - ", 1)) if ident else (None, None, None)
    el_cod, el = codigo(r.get("Elemento"))
    apl_cod = codigo(r.get("Cód. Aplicação - Fixo"), 4)[0]
    out = {"ano_arquivo": ano_arquivo, "id_origem": r["_id"], "ano_balancete": ano, "mes_balancete": mes,
           "dt_referencia": data_br(r.get("Dt. Referência")), "orgao": _txt(r.get("Órgão"), 200),
           "nr_empenho": _txt(r.get("Nr. Empenho"), 30), "ano_empenho": inteiro(r.get("Ano Empenho")),
           "modalidade_licitacao": _txt(r.get("Mod. de Licitação"), 60), "funcao": _txt(r.get("Função de Governo"), 160),
           "subfuncao": _txt(r.get("Subfunção de Governo"), 160), "fonte_recurso": _txt(r.get("Fonte de Recurso"), 160),
           "aplicacao_codigo": apl_cod.zfill(4) if apl_cod else None,
           "elemento_codigo": el_cod, "elemento": _txt(el, 300), "natureza": natureza_do_elemento(el_cod),
           "credor_tipo": tipo, "credor_cnpj": cnpj, "credor_nome": _txt(r.get("Nome do Credor"), 300) if tipo != "PF" else None}
    out.update({k: valor(r.get(col)) for k, col in _RP_VALORES.items()})
    return out


PROJECAO = {"despesas": despesa, "receitas": receita, "restos_a_pagar": resto}


def troca_ano(db: Session, tipo: str, ano_arquivo: int, linhas: Iterable[dict], lote: int = 5000) -> int:
    """Apaga a prata de (tipo, ano do arquivo) e grava as linhas do arquivo novo. Não confirma (o chamador confirma)."""
    modelo, proj = MODELOS[tipo], PROJECAO[tipo]
    db.execute(delete(modelo).where(modelo.ano_arquivo == ano_arquivo))
    n, buf = 0, []
    for r in linhas:
        buf.append(proj(r, ano_arquivo))
        if len(buf) >= lote:
            db.execute(insert(modelo), buf)
            n, buf = n + len(buf), []
    if buf:
        db.execute(insert(modelo), buf)
        n += len(buf)
    return n
