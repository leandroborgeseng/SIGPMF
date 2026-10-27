"""Conector SIL (API da Prefeitura de Franca), SOMENTE LEITURA, para enriquecimento por chave (aceite SIL1-SIL10).

Só existe UM POST permitido: o de autenticação (POST /api/authenticate, OAuth2 client_credentials). Todo o resto é GET
numa allowlist fechada (/franca/imoveis, /franca/empresas, /franca/atividade). A escrita da SIL
(.../imoveis/atualizar) é proibida e bloqueada antes de qualquer chamada de rede. Desligado sem as variáveis
SIL_BASE_URL, SIL_CLIENT_ID e SIL_CLIENT_SECRET. Segredo e token nunca vão para log, controle ou resposta.
"""
