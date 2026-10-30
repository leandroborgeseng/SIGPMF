"""Endereço do paciente da Saúde (aceite EN1, EN4, EN10–EN12; dados de referência do Geral).

Ordem das fontes (aceite, DBA 29/10): 1) `Cd_Rua` -> `Geral.dbo.Rua` (CD_RUA) com o bairro de `Geral.dbo.Bairro`
(`Cod_Bairro` = CD_BAI); número `Nr`, complemento `Complemento`, CEP `Cep` do paciente (vazio: CEP da rua).
2) `Cd_Rua` vazio -> bloco `Outra_Rua`, `Outro_Numero`, `Outro_Bairro`, `Outra_Cidade`, `Outro_Estado`, `Outro_Cep`.
3) Os Correios (#47) só completam o que continuar vazio (fora deste módulo; EN13).
`Cod_Cidade`/`Cod_Estado` são códigos internos: nunca são lidos nem exibidos (EN12, regra IM8d).

A referência (ref.geral_rua / ref.geral_bairro) é carregada uma vez por reconstrução (`carrega`) num cache do
módulo; sem referência carregada, `Cd_Rua` sem correspondência cai no bloco Outro_*. Nada daqui vai para log.
"""
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.sql_guard import garante_somente_leitura

SEM_RUA = "Rua não informada na origem"
OUTRO = ("Outra_Rua", "Outro_Numero", "Outro_Bairro", "Outra_Cidade", "Outro_Estado", "Outro_Cep")
_NAO_DIGITO = re.compile(r"\D")

# Leitura da origem Geral (só SELECT; colunas de Geral/schema.md). Tipo_Logradouro resolve o código do tipo.
SQL_RUAS = garante_somente_leitura(
    "SELECT [r].[CD_RUA], [r].[ENDERECO], [r].[Logradouro], [r].[CEP], [r].[CIDADE], [r].[UF],"
    " [r].[Cod_IBGE_Municipio], [t].[Descr_Tipo_Logradouro]"
    " FROM [dbo].[Rua] AS [r] WITH (NOLOCK)"
    " LEFT JOIN [dbo].[Tipo_Logradouro] AS [t] WITH (NOLOCK) ON [t].[Cod_Tipo_Logradouro] = [r].[Cod_Tipo_Logradouro]")
SQL_BAIRROS = garante_somente_leitura("SELECT [CD_BAI], [BAIRRO], [Cidade] FROM [dbo].[Bairro] WITH (NOLOCK)")

_REF: dict[str, dict] = {"ruas": {}, "bairros": {}}


def _t(v: Any, n: int = 300) -> str | None:
    s = " ".join(str(v).split()) if v is not None and not isinstance(v, bool) else ""
    return s[:n] or None


def _int(v: Any) -> int | None:
    try:
        return int(str(v).strip()) if v not in (None, "") and not isinstance(v, bool) else None
    except ValueError:
        return None


def cep8(v: Any) -> str | None:
    """EN4: só dígitos; exatamente 8 dígitos (não zerado) é CEP, o resto é descartado."""
    if v is None or isinstance(v, bool):
        return None
    d = _NAO_DIGITO.sub("", str(v))
    return d if len(d) == 8 and int(d) != 0 else None


def formata_cep(c: str | None) -> str | None:
    return f"{c[:5]}-{c[5:]}" if c else None


# ---------- referência (ref.geral_*) ----------
def linha_rua(d: dict) -> dict | None:
    cd = _int(d.get("CD_RUA"))
    if cd is None:
        return None
    ibge = _NAO_DIGITO.sub("", str(d.get("Cod_IBGE_Municipio") or ""))
    return {"cd_rua": cd, "tipo_logradouro": _t(d.get("Descr_Tipo_Logradouro"), 80),
            "logradouro": _t(d.get("ENDERECO")) or _t(d.get("Logradouro")), "cep": cep8(d.get("CEP")),
            "cidade": _t(d.get("CIDADE"), 120), "uf": (_t(d.get("UF"), 2) or "").upper() or None,
            "codigo_ibge": ibge if len(ibge) == 7 else None}


def linha_bairro(d: dict) -> dict | None:
    cd = _int(d.get("CD_BAI"))
    return None if cd is None else {"cd_bai": cd, "bairro": _t(d.get("BAIRRO"), 200), "cidade": _t(d.get("Cidade"), 120)}


def importa(db: Session, ruas: list[dict], bairros: list[dict]) -> dict[str, int]:
    """Upsert da referência do Geral (linhas já lidas com SQL_RUAS / SQL_BAIRROS). Só contagens no retorno."""
    rs = [x for x in (linha_rua(d) for d in ruas) if x]
    bs = [x for x in (linha_bairro(d) for d in bairros) if x]
    if rs:
        db.execute(text("""INSERT INTO ref.geral_rua (cd_rua, tipo_logradouro, logradouro, cep, cidade, uf, codigo_ibge)
            VALUES (:cd_rua, :tipo_logradouro, :logradouro, :cep, :cidade, :uf, :codigo_ibge)
            ON CONFLICT (cd_rua) DO UPDATE SET tipo_logradouro = EXCLUDED.tipo_logradouro,
              logradouro = EXCLUDED.logradouro, cep = EXCLUDED.cep, cidade = EXCLUDED.cidade, uf = EXCLUDED.uf,
              codigo_ibge = EXCLUDED.codigo_ibge, atualizado_em = now()"""), rs)
    if bs:
        db.execute(text("""INSERT INTO ref.geral_bairro (cd_bai, bairro, cidade) VALUES (:cd_bai, :bairro, :cidade)
            ON CONFLICT (cd_bai) DO UPDATE SET bairro = EXCLUDED.bairro, cidade = EXCLUDED.cidade,
              atualizado_em = now()"""), bs)
    db.commit()
    return {"ruas": len(rs), "bairros": len(bs), "descartadas": len(ruas) + len(bairros) - len(rs) - len(bs)}


def carrega(db: Session) -> None:
    """Cache da referência para a reconstrução (prata e ouro usam o mesmo resultado)."""
    _REF["ruas"] = {r["cd_rua"]: dict(r) for r in db.execute(text(
        "SELECT cd_rua, tipo_logradouro, logradouro, cep, cidade, uf, codigo_ibge FROM ref.geral_rua")).mappings()}
    _REF["bairros"] = {b["cd_bai"]: dict(b) for b in db.execute(text(
        "SELECT cd_bai, bairro, cidade FROM ref.geral_bairro")).mappings()}


def define_ref(ruas: dict | None = None, bairros: dict | None = None) -> None:
    _REF["ruas"], _REF["bairros"] = dict(ruas or {}), dict(bairros or {})


# ---------- resolução ----------
def partes(d: dict) -> dict:
    """EN10/EN11: partes estruturadas do endereço do paciente (origem_endereco: geral | outro | nenhum)."""
    rua = _REF["ruas"].get(_int(d.get("Cd_Rua")))
    if rua is not None:
        bai = _REF["bairros"].get(_int(d.get("Cod_Bairro"))) or {}
        return {"tipo_logradouro": rua.get("tipo_logradouro"), "logradouro": rua.get("logradouro"),
                "numero": _t(d.get("Nr"), 30), "complemento": _t(d.get("Complemento"), 200),
                "bairro": bai.get("bairro"), "cidade": rua.get("cidade") or bai.get("cidade"), "uf": rua.get("uf"),
                "cep": cep8(d.get("Cep")) or rua.get("cep"), "codigo_ibge": rua.get("codigo_ibge"),
                "origem_endereco": "geral"}
    if any(_t(d.get(c)) for c in OUTRO):
        return {"tipo_logradouro": None, "logradouro": _t(d.get("Outra_Rua")), "numero": _t(d.get("Outro_Numero"), 30),
                "complemento": _t(d.get("Complemento"), 200), "bairro": _t(d.get("Outro_Bairro"), 200),
                "cidade": _t(d.get("Outra_Cidade"), 120), "uf": (_t(d.get("Outro_Estado"), 2) or "").upper() or None,
                "cep": cep8(d.get("Outro_Cep")), "codigo_ibge": None, "origem_endereco": "outro"}
    return {"tipo_logradouro": None, "logradouro": None, "numero": _t(d.get("Nr"), 30),
            "complemento": _t(d.get("Complemento"), 200), "bairro": None, "cidade": None, "uf": None,
            "cep": cep8(d.get("Cep")), "codigo_ibge": None, "origem_endereco": "nenhum"}


def _rua(p: dict) -> str:
    tipo, nome = p.get("tipo_logradouro"), p.get("logradouro")
    if not nome:
        return SEM_RUA
    if tipo and not nome.casefold().startswith(tipo.casefold()):
        return f"{tipo} {nome}"
    return nome


def linhas(p: dict) -> tuple[str, str] | None:
    """EN1/EN4/EN8: linha 1 "Tipo Logradouro, número – complemento"; linha 2 "Bairro · Cidade/UF · CEP 00000-000".
    O número nunca vem antes da rua; sem nada além do placeholder, não há endereço (None)."""
    vazios = all(not p.get(k) for k in ("logradouro", "numero", "complemento", "bairro", "cidade", "uf", "cep"))
    if vazios:
        return None
    l1 = _rua(p)
    if p.get("numero"):
        l1 += f", {p['numero']}"
    if p.get("complemento"):
        l1 += f" – {p['complemento']}"
    cid = "/".join(x for x in (p.get("cidade"), p.get("uf")) if x)
    l2 = " · ".join(x for x in (p.get("bairro"), cid, f"CEP {formata_cep(p['cep'])}" if p.get("cep") else None) if x)
    return l1, l2


def texto(d: dict) -> str | None:
    ls = linhas(partes(d))
    return None if ls is None else "\n".join(x for x in ls if x)
