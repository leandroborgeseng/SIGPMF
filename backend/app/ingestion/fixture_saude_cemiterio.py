"""Fixtures SINTÉTICAS com a estrutura (nomes de tabelas/colunas) das bases Saúde e Cemitério.
Nenhum valor vem das bases reais; CPF e CNS são gerados em execução (semente fixa).

Casos Saúde (SA*) e Cemitério (C*), com o resultado esperado na silver:
SA1  paciente com CPF válido + CNS definitivo; Pacientes_CNS acrescenta um CNS provisório -> 1 cidadão, 3 identificadores
SA2  sem CPF, CNS definitivo; Pacientes_CNS acrescenta provisório -> 1 cidadão (via CNS), 2 identificadores
SA3/SA4  sem CPF; o MESMO CNS aparece em duas matrículas no Pacientes_CNS -> ambos pendentes (cns_ambiguo)
SA5  CPF e CNS inválidos; nome+nascimento+mãe iguais a SA1 -> revisão (score 0,95), inconsistências cpf/cns
SA7  só CNS provisório, sem par -> novo cidadao_id (score < 0,85), 1 identificador
SA10 CPF válido; mesmo nome e mãe que SA7, nascimento diferente -> cidadão novo
E1/E2 ESUS_Paciente_Inconsistencia -> sempre fila de revisão (E1 aponta SA1 como candidato pelo CNS)
Tabelas recusadas pedidas pela origem: Lab_UsuariosWeb e PACIENTES20200106B (S1)

C1  titular com CPF de SA1 -> vínculo 1,0 ao cidadão de SA1
C2  autorizado com CPF inválido -> pendente terceiro_cpf_invalido
C3  comprador do leilão com CPF válido novo -> novo cidadão (CPF)
C4  óbito com nome+mãe de SA2, mesmo sexo -> obito_revisao (1 candidato, sem associação)
C5  óbito com nome+mãe de SA7 e SA10 -> obito_ambiguo (2 candidatos, nenhum pré-selecionado)
C6  óbito com nome+mãe de SA1, sexo divergente -> obito_sem_candidato
C7  óbito sem MAE -> obito_sem_candidato
C8  histórico OBITO01 com a mesma chave de C4 e DT_MORTE diferente -> deduplicado, vale Obitos, divergência registrada
C9  histórico OBITO01 com chave ausente em Obitos -> entra na união -> obito_sem_candidato
Tabelas recusadas: OBITO01(CONGELADO) e Novo_Cemiterio (CM1)
Colunas proibidas presentes na origem (Hipertenso, Diabetico, COR, CAUSA_MORTIS, CODIGO_OMS, MEDICO_OBITO, CRM_MEDICO,
AAA_NAO_UTILIZADO_Senha) nunca chegam ao bronze (S3/CM2).
"""
import random

from app.identity.cns import gera_definitivo, gera_provisorio
from app.identity.cpf import digitos_verificadores

SEED = 20261026


class _G:
    def __init__(self):
        self.rng = random.Random(SEED)

    def cpf(self) -> str:
        b = f"{self.rng.randrange(10**8, 10**9):09d}"
        return b + digitos_verificadores(b)

    def cns_def(self) -> str:
        return gera_definitivo(f"{self.rng.choice('12')}{self.rng.randrange(10**9, 10**10):010d}")

    def cns_prov(self) -> str:
        while True:
            c = gera_provisorio(f"7{self.rng.randrange(10**12, 10**13):013d}")
            if c:
                return c


CLINICAS = {"Hipertenso": "S", "Diabetico": "N", "Cancer": "N", "DoencaMental": "N"}
SENSIVEIS_OBITO = {"COR": "X", "CAUSA_MORTIS": "texto sintético", "CODIGO_OMS": "Z00", "MEDICO_OBITO": "Dr Ficticio",
                   "CRM_MEDICO": "0000", "AAA_NAO_UTILIZADO_Senha": "hash-ficticio"}


def gerar() -> dict[tuple[str, str], list[dict]]:
    g = _G()
    sa1_cpf = g.cpf()
    sa1_cns, sa1_prov = g.cns_def(), g.cns_prov()
    sa2_cns, sa2_prov = g.cns_def(), g.cns_prov()
    x_cns = g.cns_def()
    sa7_prov = g.cns_prov()

    def pac(mat, nome, nasc, cpf, cns, mae, sexo, data):
        return {"Matricula": mat, "Nome": nome, "NomeSocial": None, "Cpf": cpf, "Cartao_Sus": cns, "Dt_Nasc": nasc,
                "Sexo": sexo, "Mae": mae, "Pai": None, "Cd_Rua": None, "Nr": "10", "Cod_Bairro": None, "Cep": "14400-000",
                "Complemento": "Rua Teste Saude", "Falecido": "N", "DataObito": None, "Normalizado": "N",
                "Matricula_Correta": None, "Dt_Cad": "2020-01-01", "dt_ultimaatual": data, **CLINICAS}

    D = "2026-09-15T10:00:00-03:00"
    saude = {
        ("SAUDE", "pacientes"): [
            pac(1001, "Saulo Sintetico Prado", "1970-02-03", sa1_cpf, sa1_cns, "Mae Sintetica Prado", "M", D),
            pac(1002, "Rita Ficticia Campos", "1950-05-06", None, sa2_cns, "Mae Ficticia Campos", "F", D),
            pac(1003, "Tomas Exemplo Vaz", "1985-07-08", None, None, "Mae Exemplo Vaz", "M", D),
            pac(1004, "Ugo Exemplo Vaz", "1986-09-10", None, None, "Mae Exemplo Vaz", "M", D),
            pac(1005, "Saulo Sintetico Prado", "1970-02-03", sa1_cpf[:10] + str((int(sa1_cpf[10]) + 1) % 10),
                sa1_cns[:14] + str((int(sa1_cns[14]) + 1) % 10), "Mae Sintetica Prado", "M", D),
            pac(1007, "Vilma Teste Moura", "1940-01-01", None, sa7_prov, "Mae Teste Moura", "F", D),
            pac(1010, "Vilma Teste Moura", "1962-03-03", g.cpf(), None, "Mae Teste Moura", "F", D),
        ],
        ("SAUDE", "Pacientes_CNS"): [
            {"Matricula": 1001, "Cartao_Sus": sa1_prov, "Data_Atribuicao": "2021-01-01", "Tipo_Cartao": "P"},
            {"Matricula": 1002, "Cartao_Sus": sa2_prov, "Data_Atribuicao": "2021-01-01", "Tipo_Cartao": "P"},
            {"Matricula": 1003, "Cartao_Sus": x_cns, "Data_Atribuicao": "2021-01-01", "Tipo_Cartao": "D"},
            {"Matricula": 1004, "Cartao_Sus": x_cns, "Data_Atribuicao": "2021-01-01", "Tipo_Cartao": "D"},
        ],
        ("SAUDE", "ESUS_Paciente_Inconsistencia"): [
            {"Id": 1, "Cartao_Sus": sa1_cns, "Nome_Paciente": "Saulo S Prado", "Data_Nascimento": "1970-02-03",
             "Nome_Mae": "Mae Sintetica Prado", "Data_Origem": "2026-08-01", "Status_Resolvido": "N"},
            {"Id": 2, "Cartao_Sus": None, "Nome_Paciente": "Pessoa Sem Par", "Data_Nascimento": "2000-01-01",
             "Nome_Mae": None, "Data_Origem": "2026-08-02", "Status_Resolvido": "N"},
        ],
        ("SAUDE", "Lab_UsuariosWeb"): [{"Login": "ficticio", "SenhaHash": "x"}],
        ("SAUDE", "PACIENTES20200106B"): [{"Matricula": 1}],
    }

    def obito(cod, quadra, sep, nome, mae, sexo, dt, **extra):
        return {"COD_CEMITERIO": cod, "QUADRA": quadra, "SEPULTURA": sep, "GAVETA": 1, "LADO": "A", "SEQUENCIA": 1,
                "FALECIDO": nome, "PAI": None, "MAE": mae, "SEXO": sexo, "NATURAL": "Franca", "EST_CIVIL": None,
                "IDADE": None, "DT_MORTE": dt, "DT_SEPULTADO": dt, "Data_Inclusao": None, "Data_Ultima_Alteracao": None,
                **SENSIVEIS_OBITO, **extra}

    cem = {
        ("CEMITERIO", "Titulares"): [
            {"Cod_Cemiterio": 1, "Quadra": "Q1", "Sepultura": "S1", "Sequencia": 1, "Titular": "Saulo Sintetico Prado",
             "CPF": f"{sa1_cpf[:3]}.{sa1_cpf[3:6]}.{sa1_cpf[6:9]}-{sa1_cpf[9:]}", "Endereco": "Quadra 1, lote 3 (texto livre)",
             "Ativo": "S", "Inclusao": "2019-04-04", "RG": "ficticio", "Fone": "(16) 90000-0001", "Email": "x@exemplo.invalid"}],
        ("CEMITERIO", "Pessoas_Autorizadas"): [
            {"Cod_Cemiterio": 1, "Quadra": "Q1", "Sepultura": "S1", "Sequencia": 1, "Nome": "Autorizado Sem Cpf Valido",
             "CPF": "1" * 11, "Parentesco": "Filho", "Inclusao": "2019-04-05", "RG": "ficticio"}],
        ("CEMITERIO", "Leilao"): [
            {"Cod_Cemiterio": 2, "Quadra": "Q9", "Sepultura": "S9", "DataVenda": "2024-02-02", "Nome": "Comprador Ficticio Lopes",
             "CPF": g.cpf(), "DataLiberacao": "2024-02-03"}],
        ("CEMITERIO", "Obitos"): [
            obito(1, "Q2", "S1", "Rita Ficticia Campos", "Mae Ficticia Campos", "F", "2026-01-10"),
            obito(1, "Q2", "S2", "Vilma Teste Moura", "Mae Teste Moura", "F", "2026-02-11"),
            obito(1, "Q2", "S3", "Saulo Sintetico Prado", "Mae Sintetica Prado", "F", "2026-03-12"),
            obito(1, "Q2", "S4", "Pessoa Sem Mae", None, "M", "2026-04-13"),
        ],
        ("CEMITERIO", "AAA_NAO_UTILIZADO_OBITO01"): [
            obito(1, "Q2", "S1", "Rita Ficticia Campos", "Mae Ficticia Campos", "F", "2025-12-31"),
            obito(1, "Q7", "S7", "Falecido Antigo Sintetico", "Mae Antiga", "M", "1979-05-05"),
        ],
        ("CEMITERIO", "OBITO01(CONGELADO)"): [obito(1, "Q0", "S0", "X", "Y", "M", "1970-01-01")],
        ("CEMITERIO", "Novo_Cemiterio"): [{"x": 1}],
    }
    return {"saude": saude, "cemiterio": cem}


ESPERADO = {
    "saude": {"bronze_registros": 13, "silver_cidadaos": 4, "silver_identificadores": 7, "silver_vinculos": 4,
              "silver_correlacoes_pendentes": 5, "silver_inconsistencias": 2, "tabelas_recusadas": 2},
    # #10 (TL): históricas AAA_NAO_UTILIZADO_* fora da política -> 2 linhas a menos na bronze, sem o dedupe histórico
    "saude+cemiterio": {"bronze_registros": 20, "silver_cidadaos": 5, "silver_identificadores": 8, "silver_vinculos": 6,
                        "silver_correlacoes_pendentes": 10, "silver_inconsistencias": 3, "tabelas_recusadas": 4},
}
