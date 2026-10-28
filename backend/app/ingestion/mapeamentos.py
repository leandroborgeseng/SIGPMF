"""Mapeamento de colunas da origem para os campos canônicos usados na silver.

Cada entrada: domínio, chave (coluna ou tupla de colunas), campo incremental, papel e campos canônicos.
Papéis: pessoa (cadastro), cns_extra (CNS adicionais de uma pessoa), revisao (sempre vai à fila),
terceiro (titular/autorizado/comprador: só CPF válido associa), obito (nunca associa sozinho).
Um campo canônico pode juntar várias colunas (tupla) como texto livre.
"""
from app.connectors.sqlserver.politicas import HISTORICAS_OBITO

CANONICOS = ("nome", "data_nascimento", "cpf", "nome_mae", "endereco", "atualizado_em")
CHAVE_SEPULTAMENTO = ("COD_CEMITERIO", "QUADRA", "SEPULTURA", "GAVETA", "LADO", "SEQUENCIA")

_OBITO = {"dominio": "cemiterio", "chave": CHAVE_SEPULTAMENTO, "campo_incremental": "Data_Ultima_Alteracao",
          "papel": "obito",
          "campos": {"nome": "FALECIDO", "nome_mae": "MAE", "nome_pai": "PAI", "sexo": "SEXO", "data_obito": "DT_MORTE",
                     "atualizado_em": "Data_Ultima_Alteracao"}}

# Tributário (onda 2): papel "fato" — não correlaciona pessoa; vira tabelas próprias da prata (app.silver.tributario)
_FATO = {"dominio": "tributario", "papel": "fato", "campos": {}}

_FATO_SAUDE = {"dominio": "saude", "papel": "fato", "campos": {}}

MAPEAMENTOS: dict[tuple[str, str], dict] = {
    # ---- Saúde, fatos (onda 3): atendimentos, agenda, vacinas e dimensões; ligados ao cidadão pela matrícula ----
    ("SAUDE", "Consultas"): {**_FATO_SAUDE, "chave": ("Cod_Paciente", "Cons_Paciente"), "campo_incremental": "Dt_Atend"},
    ("SAUDE", "Agendamento_Consulta"): {**_FATO_SAUDE, "chave": ("Cod_Ubs", "Cod_Prof", "Cod_Pac", "Dt_Cons", "Hr_Cons"),
                                        "campo_incremental": "Data_Hora_SIGS"},
    ("SAUDE", "Vacina"): {**_FATO_SAUDE, "chave": ("Matricula", "Cod_Vacina", "Sequencia"), "campo_incremental": "DataGravacao"},
    ("SAUDE", "Unidades"): {**_FATO_SAUDE, "chave": "Codigo", "campo_incremental": None},
    ("SAUDE", "Aux_Vacina"): {**_FATO_SAUDE, "chave": "Cod_Vacina", "campo_incremental": None},
    ("SAUDE", "Aux_Especialidades"): {**_FATO_SAUDE, "chave": "Codi_Espe", "campo_incremental": None},
    ("IPTU", "Cadastro"): {**_FATO, "chave": "CODI", "campo_incremental": "Data_Alte"},
    ("IPTU", "Cadastro_Possuidor"): {**_FATO, "chave": ("CODI", "CPF_CNPJ"), "campo_incremental": "Data_Inclusao"},
    ("DIVIDA", "Divida"): {**_FATO, "chave": ("CODI", "SEQU", "ANO"), "campo_incremental": "DT_CALCULO"},
    ("FINANCEIRO", "Financeiro_Exercicio"): {**_FATO, "chave": ("Exercicio", "Tributo", "Nr_Carne", "Sequencia",
                                                               "Referencia", "Renovacao", "Parcela", "Duplicidade"),
                                             "campo_incremental": "Data_Ultima_Alteracao_Registro"},
    ("DEMO_SAUDE", "prontuario"): {
        "dominio": "saude", "chave": "id", "campo_incremental": "atualizado_em",
        "campos": {"nome": "nome", "data_nascimento": "nascimento", "cpf": "cpf", "nome_mae": "nome_mae",
                   "endereco": "endereco", "atualizado_em": "atualizado_em"}},
    ("DEMO_CEMITERIO", "sepultamento"): {
        "dominio": "cemiterio", "chave": "cod", "campo_incremental": "dt_alteracao",
        "campos": {"nome": "nome", "data_nascimento": "dt_nasc", "cpf": "cpf_cnpj", "nome_mae": "mae",
                   "endereco": "logradouro", "atualizado_em": "dt_alteracao"}},
    # ---- Saúde (aceite S1-S8) ----
    ("SAUDE", "pacientes"): {
        "dominio": "saude", "chave": "Matricula", "campo_incremental": "dt_ultimaatual", "papel": "pessoa",
        "rowversion": None,  # S2: usar a coluna rowversion no lugar do hash quando existir na origem
        "campos": {"nome": "Nome", "data_nascimento": "Dt_Nasc", "cpf": "Cpf", "cns": "Cartao_Sus", "nome_mae": "Mae",
                   "nome_pai": "Pai", "sexo": "Sexo", "endereco": ("Complemento", "Nr", "Cep"),
                   "atualizado_em": "dt_ultimaatual", "pista_normalizado": "Normalizado",
                   "pista_matricula_correta": "Matricula_Correta"}},
    ("SAUDE", "Pacientes_CNS"): {
        "dominio": "saude", "chave": ("Matricula", "Cartao_Sus"), "campo_incremental": "Data_Atribuicao",
        "papel": "cns_extra", "campos": {"chave_pessoa": "Matricula", "cns": "Cartao_Sus",
                                         "atualizado_em": "Data_Atribuicao"}},
    ("SAUDE", "ESUS_Paciente_Inconsistencia"): {
        "dominio": "saude", "chave": "Id", "campo_incremental": "Data_Origem", "papel": "revisao",
        "campos": {"nome": "Nome_Paciente", "data_nascimento": "Data_Nascimento", "nome_mae": "Nome_Mae",
                   "cns": "Cartao_Sus", "atualizado_em": "Data_Origem"}},
    # ---- Cemitério (aceite CM1-CM11) ----
    ("CEMITERIO", "Obitos"): {**_OBITO, "prioridade": 0},
    **{("CEMITERIO", h): {**_OBITO, "prioridade": 1} for h in HISTORICAS_OBITO},
    ("CEMITERIO", "Titulares"): {
        "dominio": "cemiterio", "chave": ("Cod_Cemiterio", "Quadra", "Sepultura", "Sequencia"),
        "campo_incremental": "Inclusao", "papel": "terceiro",
        "campos": {"nome": "Titular", "cpf": "CPF", "endereco": "Endereco", "atualizado_em": "Inclusao"}},
    ("CEMITERIO", "Pessoas_Autorizadas"): {
        "dominio": "cemiterio", "chave": ("Cod_Cemiterio", "Quadra", "Sepultura", "Sequencia"),
        "campo_incremental": "Inclusao", "papel": "terceiro",
        "campos": {"nome": "Nome", "cpf": "CPF", "atualizado_em": "Inclusao"}},
    ("CEMITERIO", "Leilao"): {
        "dominio": "cemiterio", "chave": ("Cod_Cemiterio", "Quadra", "Sepultura", "DataVenda"),
        "campo_incremental": "DataVenda", "papel": "terceiro",
        "campos": {"nome": "Nome", "cpf": "CPF", "atualizado_em": "DataVenda"}},
}

NOMES_SISTEMAS = {"DEMO_SAUDE": "Saúde — Prontuário", "DEMO_CEMITERIO": "Cemitério — Sepultamentos",
                  "SAUDE": "Saúde (sintético, estrutura real)", "CEMITERIO": "Cemitério (sintético, estrutura real)",
                  "IPTU": "IPTU — imóveis e possuidores", "DIVIDA": "Dívida ativa", "FINANCEIRO": "Lançamentos tributários"}


def chave_origem(m: dict, dados: dict) -> str:
    ch = m["chave"]
    if isinstance(ch, tuple):
        return "|".join("" if dados.get(c) is None else str(dados.get(c)) for c in ch)
    return str(dados[ch])


def canonico(sistema: str, tabela: str, dados: dict) -> dict:
    m = MAPEAMENTOS.get((sistema, tabela))
    if not m:
        return {k: dados.get(k) for k in CANONICOS}
    out = {}
    for k, orig in m["campos"].items():
        if isinstance(orig, tuple):
            partes = [str(dados[c]) for c in orig if dados.get(c) not in (None, "")]
            out[k] = ", ".join(partes) or None
        else:
            out[k] = dados.get(orig)
    return out


def papel(sistema: str, tabela: str) -> str:
    return MAPEAMENTOS.get((sistema, tabela), {}).get("papel", "pessoa")
