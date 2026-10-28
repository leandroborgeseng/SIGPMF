"""Políticas de dados (onda 7, PL1): classificação, finalidade/base legal, retenção, tratamento e quem vê, por conjunto.

Configuração VERSIONADA no código (muda por PR, com revisão), não editável pela tela. Prazos e bases legais são a
proposta técnica; a palavra final é do encarregado (DPO) e da Procuradoria — a tela avisa isso.
"""
REGRAS = [
    {"camada": "Bronze", "titulo": "Só a versão atual de cada registro fica",
     "texto": "Versões antigas substituídas há mais de {bronze_dias} dias são apagadas todo dia. A versão atual "
              "fica (a prata é reconstruída dela)."},
    {"camada": "Prata", "titulo": "Enquanto houver finalidade",
     "texto": "Cadastros e fatos ligados ao cidadão. Revisão anual de necessidade com o DPO."},
    {"camada": "Ouro", "titulo": "Agregados sem identificação",
     "texto": "Painéis sem CPF nem nome; grupos de saúde com menos de 5 casos não aparecem."},
    {"camada": "Auditoria", "titulo": "Registro de acessos imutável",
     "texto": "Quem viu a ficha de quem, quando e por qual tela. Não pode ser alterado nem apagado (trigger no banco)."},
]

PUB, INT, PES, SEN = "publico", "interno", "pessoal", "sensivel"

CONJUNTOS = [
    {"nome": "Cadastro de pessoas", "origem": "Saúde (pacientes), ISS (munícipes), Cemitério", "classe": PES,
     "base": "Execução de políticas públicas · LGPD art. 7º, III e art. 23",
     "retencao": "versão atual / enquanto houver finalidade / sem identificação",
     "tratamento": "CPF mascarado; revelação só admin_lgpd com motivo", "quem": "Todos os perfis (mascarado)"},
    {"nome": "Imóveis e possuidores", "origem": "IPTU", "classe": PES,
     "base": "Tributação · art. 7º, II e III", "retencao": "versão atual / permanente / agregado",
     "tratamento": "Nunca cria cidadão; liga só por CPF já correlacionado", "quem": "Todos os perfis; vínculos só admin/admin_lgpd"},
    {"nome": "Lançamentos e dívida ativa", "origem": "Financeiro, Dívida", "classe": PES,
     "base": "Cobrança de tributos · art. 7º, II", "retencao": "exercício atual e anterior / 10 anos após extinção / agregado",
     "tratamento": "Ranking de saldos por imóvel, sem nome", "quem": "Ficha: todos os perfis, auditado"},
    {"nome": "Atendimentos, agenda e vacinação", "origem": "Saúde", "classe": SEN,
     "base": "Tutela da saúde · art. 11, II, b e f", "retencao": "12 a 24 meses na origem lida / enquanto houver finalidade / agregado",
     "tratamento": "Sem conteúdo clínico (queixa, HMA, suspeita, exame, sinais vitais fora); grupos < 5 ocultos",
     "quem": "Ficha: todos os perfis, auditado"},
    {"nome": "Óbitos", "origem": "Cemitério", "classe": PES,
     "base": "Atualização cadastral · art. 7º, III", "retencao": "versão atual / permanente / agregado",
     "tratamento": "Causa da morte nunca entra; associação só por confirmação humana", "quem": "admin_lgpd confirma"},
    {"nome": "Auditoria de acessos", "origem": "SIGPMF", "classe": INT,
     "base": "Segurança e prestação de contas · art. 37 e art. 46", "retencao": "imutável (prazo legal com o DPO)",
     "tratamento": "Append-only por trigger", "quem": "admin, admin_lgpd"},
]

AREAS = ["Cadastro", "Impostos", "Saúde", "Vínculos", "Auditoria"]
V, T, N = "ver", "total", "nada"
PERFIS = [
    {"perfil": "admin", "celulas": [V, V, V, V, V]},
    {"perfil": "admin_lgpd", "celulas": [V, V, V, V, V]},
    {"perfil": "operador", "celulas": [V, V, V, N, N]},
    {"perfil": "leitura", "celulas": [V, V, V, N, N]},
    {"perfil": "painéis (gestão)", "celulas": [T, T, T, N, N]},
]
