"""Mapeamento de colunas da origem para os campos canônicos usados na silver.
Hoje só os sistemas sintéticos; cada Markdown de mapeamento real acrescenta uma entrada."""
CANONICOS = ("nome", "data_nascimento", "cpf", "nome_mae", "endereco", "atualizado_em")

MAPEAMENTOS: dict[tuple[str, str], dict] = {
    ("DEMO_SAUDE", "prontuario"): {
        "dominio": "saude", "chave": "id", "campo_incremental": "atualizado_em",
        "campos": {"nome": "nome", "data_nascimento": "nascimento", "cpf": "cpf", "nome_mae": "nome_mae",
                   "endereco": "endereco", "atualizado_em": "atualizado_em"}},
    ("DEMO_CEMITERIO", "sepultamento"): {
        "dominio": "cemiterio", "chave": "cod", "campo_incremental": "dt_alteracao",
        "campos": {"nome": "nome", "data_nascimento": "dt_nasc", "cpf": "cpf_cnpj", "nome_mae": "mae",
                   "endereco": "logradouro", "atualizado_em": "dt_alteracao"}},
}

NOMES_SISTEMAS = {"DEMO_SAUDE": "Saúde — Prontuário", "DEMO_CEMITERIO": "Cemitério — Sepultamentos"}


def canonico(sistema: str, tabela: str, dados: dict) -> dict:
    m = MAPEAMENTOS.get((sistema, tabela))
    if not m:
        return {k: dados.get(k) for k in CANONICOS}
    return {k: dados.get(orig) for k, orig in m["campos"].items()}


def campos_origem_mapeados(sistema: str, tabela: str) -> set[str]:
    m = MAPEAMENTOS.get((sistema, tabela))
    return set(m["campos"].values()) | {m["chave"]} if m else set()
