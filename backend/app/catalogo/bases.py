"""Catálogo das bases do servidor SQL Server examinadas pelo DBA (fonte: mapeamentos/triagem-bancos.md).

Só NOMES de bancos e descrições curtas; nenhuma linha de dado. O seed é idempotente (upsert por nome, sem DELETE).

Status:
- implementado: conector pronto; `sistemas` lista os códigos gravados em bronze.registros.sistema_origem,
  silver.vinculos.sistema, silver.correlacoes_pendentes.sistema e gold.cidadao_dados.sistema;
  `conjunto` é o conjunto de carga (OrigemMock / executar_demo) e o valor de controle.execucoes.sistema.
- mapeado: na fila aprovada pelo Tech Lead (prioridade = ordem na fila).
- triado: analisado (schema) ou pré-triado pelo nome, sem conector previsto ainda.
- excluido: descartado (credenciais, logs, vazio, espelho, sem pessoa).
"""
from sqlalchemy import text
from sqlalchemy.engine import Connection

STATUS = ("implementado", "mapeado", "triado", "excluido")

# (nome, grupo, triagem, status, prioridade, sistemas, conjunto, descricao)
_I, _M, _T, _X = STATUS
_P, _B, _C = "painel_cidadao", "indicadores", "descartavel"
_A, _N = "schema", "nome"

BASES: list[tuple] = [
    # ---- implementados (MVP) ----
    ("Saude", _P, _A, _I, 1, ["SAUDE"], "saude", "Pacientes (fonte-mestre de pessoa), CNS e inconsistências e-SUS"),
    ("Cemiterio", _P, _N, _I, 2, ["CEMITERIO"], "cemiterio", "Óbitos, titulares, autorizados e leilão de sepulturas"),
    # ---- fila aprovada pelo Tech Lead ----
    ("ISS", _P, _A, _I, 3, ["ISS"], "iss", "Munícipe: ISS.Municipes (fonte decidida; Geral é espelho)"),
    ("CadastroUnico", _P, _A, _M, 4, [], None, "Cadastro multi-secretaria; núcleo cad_CadastroSimples"),
    ("Protocolo_WEB", _P, _A, _M, 5, [], None, "Protocolo web: users e protocols (sem password/remember_token)"),
    ("Labor", _P, _A, _M, 6, [], None, "Laboratório: Cliente (sem CID nem senhas)"),
    ("IPTU", _P, _A, _I, 7, ["IPTU"], "iptu", "Cadastro imobiliário e possuidores do IPTU (fatos, sem criar cidadão)"),
    ("Divida", _P, _A, _I, 8, ["DIVIDA"], "divida", "Dívida ativa (débitos do cidadão no painel)"),
    ("Financeiro", _P, _A, _I, 9, ["FINANCEIRO"], "financeiro", "Lançamentos tributários do exercício atual e anterior"),
    ("Alvara", _B, _A, _T, 9, [], None, "Alvarás (sem CPF); indicador de licenciamento"),
    ("IntegracaoJuridicoDivida", _P, _A, _T, 10, [], None, "Dívida ativa judicial: cadastros, CDAs, acordos (painel)"),
    # ---- analisados, sem fila ----
    ("Ambulatorio", _P, _A, _T, None, [], None, "Medicina do trabalho/exames admissionais"),
    ("Apacs", _P, _A, _T, None, [], None, "APAC/AIH do SUS por matrícula (→ Saude)"),
    ("Atend", _P, _A, _T, None, [], None, "Fila de senhas de atendimento; tempo de espera"),
    ("Educacao", _P, _A, _T, None, [], None, "Bolsa Universitária, Adote, UAB, Primeira Chance"),
    ("GED", _P, _A, _T, None, [], None, "Índice de documentos digitalizados (cemitério)"),
    ("Gesad", _P, _A, _T, None, [], None, "Solicitações internas, emissão de RG, Procon"),
    ("IntegracaoSaudeTerceiros", _P, _A, _T, None, [], None, "Dispensação de medicamentos (→ Saude)"),
    ("Iptu_Anteriores", _P, _A, _T, None, [], None, "Fotos anuais do cadastro de IPTU (histórico)"),
    ("IPTU_Isencao", _P, _A, _T, None, [], None, "Isenção de IPTU (sistema novo, vazio)"),
    ("ITBI", _P, _A, _T, None, [], None, "ITBI: transmissões de imóveis"),
    ("PetSaude", _P, _A, _T, None, [], None, "PetSaúde (pouco usado)"),
    ("Raat", _P, _A, _T, None, [], None, "Acidentes de trabalho"),
    ("Transporte_Alternativo", _P, _A, _T, None, [], None, "Motoristas e veículos do transporte alternativo"),
    ("Ultrassom", _P, _A, _T, None, [], None, "Exames de ultrassom (paciente externo)"),
    ("Rendas", _P, _A, _T, None, [], None, "Taxas e receitas diversas"),
    ("Fundos_Municipais", _B, _A, _T, None, [], None, "Doações a fundos municipais"),
    ("Obras", _B, _A, _T, None, [], None, "Projetos e numeração de imóveis"),
    ("ProcJur", _B, _A, _T, None, [], None, "Processos jurídicos e execuções fiscais"),
    ("Rodoviaria", _B, _A, _T, None, [], None, "Reservas e motoristas da rodoviária"),
    ("Simples_Nacional", _B, _A, _T, None, [], None, "Empresas do Simples (só CNPJ)"),
    ("Transito", _B, _A, _T, None, [], None, "Multas de trânsito"),
    # ---- analisados e descartados ----
    ("Geral", _P, _A, _X, None, [], None, "Espelho contido no ISS.Municipes — não ler (decisão 25/10/2026)"),
    ("AtendSaude", _P, _A, _X, None, [], None, "Fila de senhas vazia (0 linhas)"),
    ("Novo_Cemiterio", _P, _A, _X, None, [], None, "Migração abandonada (0 linhas)"),
    ("ManutHardware", _C, _A, _X, None, [], None, "Chamados de TI internos"),
    ("Senhas", _C, _A, _X, None, [], None, "Credenciais — fora"),
    ("Telefonia", _C, _A, _X, None, [], None, "Sem pessoa"),
    ("Traces", _C, _A, _X, None, [], None, "Monitoramento do DBA"),
    ("TrendVP", _C, _A, _X, None, [], None, "Antivírus/vulnerabilidades"),
    # ---- pré-triagem pelo nome ----
    *[(n, _P, _N, _T, None, [], None, "Pré-triagem pelo nome") for n in ("Bibliotecario", "Concursos")],
    *[(n, _B, _N, _T, None, [], None, "Pré-triagem pelo nome (indicadores)") for n in (
        "ArquivoHistorico", "Baixas", "Cert_Emit", "ConselhosMunicipais", "Controle_Despesa", "Controle_Documentacao",
        "Estoque", "Fiscalizacao_Rendas")],
    *[(n, _C, _N, _X, None, [], None, "Descartável (pré-triagem pelo nome)") for n in (
        "API_Email", "API_Usuario", "Baixas_Ant", "CAL", "Central", "Correios", "DbPPrioritariosIdentityMunicipios",
        "DBSENIOR", "DBSENIOR_EDOCS", "DBSENIOR_ESOCIAL", "DBSENIOR_PORTAL", "Divida_Anteriores", "DWConfiguration",
        "DWDiagnostics", "DWQueue", "EFWeb", "Estoque_Anterior", "Financeiro_Anteriores", "Folpag_", "Internet",
        "LogSistemas", "LogSistemas_Anteriores", "LogSistemas_Ate2014")],
]

CAMPOS = ("nome", "grupo", "triagem", "status", "prioridade", "sistemas", "conjunto", "descricao")

_UPSERT = text("""
INSERT INTO controle.bases_catalogo (nome, grupo, triagem, status, prioridade, sistemas, conjunto, descricao)
VALUES (:nome, :grupo, :triagem, :status, :prioridade, CAST(:sistemas AS jsonb), :conjunto, :descricao)
ON CONFLICT (nome) DO NOTHING
""")  # só insere bases novas: nunca altera (nem rebaixa) linha existente - decisão do TL no #8


def semeia(conn: Connection) -> int:
    """Insere as bases que ainda não existem. Nunca altera nem apaga linhas existentes."""
    import json
    for b in BASES:
        p = dict(zip(CAMPOS, b, strict=True))
        p["sistemas"] = json.dumps(p["sistemas"])
        conn.execute(_UPSERT, p)
    return len(BASES)
