"""Regra isolada de processo Concluído no bloco da ficha.

Ainda não há amostra real do SEI de Franca que fixe qual tarefa ou texto marca a
conclusão. A regra lê só configuração e não chama o SEI:

- SIGPMF_SEI_TAREFAS_CONCLUIDO: ids de tarefa, separados por vírgula
- SIGPMF_SEI_TEXTO_CONCLUIDO: trechos da descrição, separados por `|`

Sem nenhuma das duas, todo processo fica "Em andamento". A comparação do texto
ignora acento e maiúsculas.
"""
import os
import unicodedata

EM_ANDAMENTO = "Em andamento"
CONCLUIDO = "Concluído"


def _norm(v: str | None) -> str:
    if not v:
        return ""
    s = unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode()
    return " ".join(s.casefold().split())


def tarefas_concluido() -> frozenset[str]:
    bruto = os.environ.get("SIGPMF_SEI_TAREFAS_CONCLUIDO") or ""
    return frozenset(p.strip() for p in bruto.split(",") if p.strip())


def textos_concluido() -> tuple[str, ...]:
    bruto = os.environ.get("SIGPMF_SEI_TEXTO_CONCLUIDO") or ""
    return tuple(t for t in (_norm(p) for p in bruto.split("|")) if t)


def concluido(id_tarefa: str | None, descricao: str | None) -> bool:
    if id_tarefa and str(id_tarefa).strip() in tarefas_concluido():
        return True
    texto = _norm(descricao)
    if not texto:
        return False
    return any(t in texto for t in textos_concluido())


def status_processo(id_tarefa: str | None, descricao: str | None) -> str:
    return CONCLUIDO if concluido(id_tarefa, descricao) else EM_ANDAMENTO
