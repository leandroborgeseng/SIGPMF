"""Máscara de nomes e dados de empresa da SIL (LGPD, SIL16).

Regra única usada no /tributario e no explorador de vínculos: razão social, endereço e bairro só saem em claro
quando `revela` é verdadeiro. Quem decide `revela` é a rota (perfil + motivo + auditoria), nunca este módulo."""

PERFIL_REVELA_EMPRESA = "admin_lgpd"


def mascara_nome(nome: str | None) -> str | None:
    """Mantém só a inicial de cada palavra: "FULANO DE TAL" -> "F***** D* T**"."""
    if not nome:
        return nome
    return " ".join(w[0] + "*" * (len(w) - 1) for w in str(nome).split())


def mascara_texto(v: str | None, revela: bool) -> str | None:
    return v if revela or not v else "***"
