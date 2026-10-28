"""Projeção para a prata do que vem da SIL (v1) e visão por perfil (SIL6, SIL8, SIL11).

- Documento (CNPJCPF/CNPJCPFResponsavel) comparado só por dígitos; CPF válido -> vínculo com o cidadão
  (cidadao_id = uuid5 do CPF, a mesma regra da prata); inválido -> pendência.
- Débito: só o indicador PossuiDebito (S/N) vira booleano; nenhum valor financeiro é inferido (parcelas fora da v1).
- Telefone e e-mail ficam na prata, mas só admin_lgpd vê. CPF/CNPJ em claro só para admin_lgpd; os demais veem
  mascarado. Nada daqui vai para log."""
from app.identity.correlacao import cidadao_id_por_cpf
from app.identity.cpf import mascara, valido

PERFIS_REVELAM = frozenset({"admin_lgpd"})


def digitos(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def debito(v) -> bool | None:
    s = str(v or "").strip().upper()
    return True if s == "S" else False if s == "N" else None


def _mascara_doc(d: str) -> str | None:
    if not d:
        return None
    if len(d) == 11:
        return mascara(d)
    return f"{d[:2]}.***.***/****-{d[-2:]}" if len(d) == 14 else "•••"


def projeta_empresa(item: dict) -> dict:
    """Item de /franca/empresas ou de consultarlote (empresa) -> linha da prata."""
    doc = digitos(item.get("CNPJCPF"))
    tipo = "cpf" if len(doc) == 11 else "cnpj" if len(doc) == 14 else "invalido"
    cid = cidadao_id_por_cpf(doc) if tipo == "cpf" and valido(doc) else None
    return {"ccm": str(item.get("Ccm") or "").strip() or None, "documento": doc or None, "tipo_documento": tipo,
            "cidadao_id": cid, "pendencia": None if (tipo == "cnpj" or cid) else "documento_invalido",
            "nome": item.get("Nome"), "possui_debito": debito(item.get("PossuiDebito")),
            "email": item.get("Email") or None, "telefone": item.get("Telefone") or None}


def projeta_imovel(item: dict) -> dict:
    """Item de /franca/imoveis -> linha da prata (ouro recebe só possui_debito e o vínculo do responsável)."""
    doc = digitos(item.get("CNPJCPFResponsavel"))
    cid = cidadao_id_por_cpf(doc) if len(doc) == 11 and valido(doc) else None
    return {"documento_responsavel": doc or None, "cidadao_id_responsavel": cid,
            "pendencia": None if cid or len(doc) == 14 else "documento_invalido",
            "possui_debito": debito(item.get("PossuiDebito"))}


def visao(linha: dict, perfil: str) -> dict:
    """O que cada perfil vê: só admin_lgpd vê CPF/CNPJ em claro, e-mail e telefone."""
    if perfil in PERFIS_REVELAM:
        return dict(linha)
    out = dict(linha)
    for k in ("documento", "documento_responsavel"):
        if k in out:
            out[k] = _mascara_doc(out[k] or "")
    for k in ("email", "telefone"):
        if k in out:
            out[k] = "••• (oculto)" if out[k] else None
    return out


def ouro(linha: dict) -> dict:
    """SIL8 + decisão 27/10: no ouro, só o indicador de débito e o vínculo do responsável/empresa ao cidadão."""
    return {"cidadao_id": linha.get("cidadao_id") or linha.get("cidadao_id_responsavel"),
            "possui_debito": linha.get("possui_debito")}
