"""Conector SEI (PRODESP/ROTA), SOMENTE LEITURA (aceite SEI1-SEI8).

Token Bearer do IdP SP (client_credentials) e cinco cabeçalhos fixos, tudo por variável de ambiente
(SIGPMF_SRC_SEI_*). Nenhum endereço no código: sem as URLs o conector fica inerte (409). A homologação só é usada com
SIGPMF_SRC_SEI_USAR_HOMOLOGACAO=true e nunca serve de fallback para a oficial (nem o contrário). Só GETs numa allowlist
fechada; conteúdo e anexos de documentos, contatos, usuários e qualquer escrita são recusados antes da rede.
"""
