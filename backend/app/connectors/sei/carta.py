"""De-para do tipo de processo do SEI para o serviço da Carta de Serviços de Franca.

Tabela simples: a chave é o nome do tipo sem acento, pontuação nem diferença de
maiúsculas. Sem correspondência, a ficha mostra o tipo que veio do SEI.
"""
import unicodedata

# Acrescente uma linha por serviço novo. O valor é o rótulo exibido na ficha.
DEPARA_CARTA: dict[str, str] = {
    "corte de arvore": "Corte de árvore",
    "autorizacao para corte de arvore": "Corte de árvore",
    "corte de arvore em via publica": "Corte de árvore",
    "certidao de area construida": "Certidão de área construída",
    "certidao de area construida habite se": "Certidão de área construída",
}


def _chave(tipo: str) -> str:
    s = unicodedata.normalize("NFKD", tipo).encode("ascii", "ignore").decode()
    limpo = "".join(ch if ch.isalnum() else " " for ch in s)
    return " ".join(limpo.casefold().split())


def rotulo_servico(tipo_sei: str | None) -> str | None:
    """Rótulo da Carta quando houver de-para; senão o próprio tipo do SEI."""
    if tipo_sei is None:
        return None
    bruto = str(tipo_sei).strip()
    if not bruto:
        return None
    return DEPARA_CARTA.get(_chave(bruto), bruto)
