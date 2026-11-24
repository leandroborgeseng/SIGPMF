# Portal do Cidadão

Entrada pública `/cidadao` no mesmo frontend do SIGPMF, com shell próprio para celular. A consulta interna de servidores continua protegida em `/simulador` e ganhou o nome **Consulta interna de cadastros** no menu. O menu agora separa Plano de Governo, Informações estratégicas, Serviços ao cidadão, Bases e integrações e TI.

## Fluxo implementado

1. Cidadão informa CPF, data de nascimento e nome completo da mãe, em POST, sem esses dados na URL.
2. O backend confere CPF normalizado em `silver.identificadores`, nascimento em `silver.cidadaos` e nome da mãe em `gold.cidadao_dados` vigente. Normaliza maiúsculas, acentos e espaços, sem correspondência aproximada. Cadastro falecido, divergente ou sem e-mail único não abre sessão.
3. Código aleatório de seis dígitos é enviado exclusivamente ao e-mail já existente na mesma base Gold. A resposta externa é igual para cadastro encontrado e não encontrado. O código nunca é retornado pela API ou gravado em claro. Não se aceita um e-mail informado no formulário como prova de identidade.
4. Código válido, com até cinco tentativas e validade de dez minutos, cria sessão própria de quinze minutos. Desafio tem uso único. Sessão usa token opaco aleatório armazenado como HMAC, cookie HttpOnly/SameSite=Strict/Secure em produção e proteção CSRF nas operações autenticadas. JWT de servidor não funciona no portal.
5. Cidadão consulta apenas seus dados cadastrais permitidos, imóveis com posse atual e lançamentos vinculados ao seu identificador de sessão. Não escolhe CPF ou ID de outra pessoa para a consulta. Não expõe dados clínicos, nome da mãe ou registros de outros cidadãos. Consultas e emissão são auditadas.

Tentativas são limitadas no banco, entre processos: seis identificações por CPF e trinta operações sensíveis por IP a cada quinze minutos. Códigos, respostas cadastrais e CPF não ficam em logs de aplicação ou na tabela efêmera. Registros expirados são limpos oportunisticamente. Consultas não ficam em cache. A rota de demonstração é apenas estado local da interface, com dados fictícios visivelmente marcados; não cria sessão nem usa endpoints de emissão.

## Ativação necessária no ambiente oficial

- Publicar esta revisão e executar `alembic upgrade head` (revisões 0049 e 0050). A revisão 0050 cria `controle.portal_acessos`.
- Conferir cobertura e procedência dos campos `nome_mae` e `email`, marcados `mais_recente`, na Gold. Se o contato não estiver integrado ou for divergente, o cidadão precisará atualizar o cadastro no atendimento. O módulo não inventa contatos nem altera correlações existentes.
- Configurar `PORTAL_SMTP_HOST`, `PORTAL_SMTP_PORT` (465), `PORTAL_SMTP_FROM` e credenciais do serviço autorizado de envio. Conexão SMTP usa TLS. Sem servidor/remetente, o acesso real permanece indisponível. É necessário testar entrega com um cadastro de homologação.
- Servir o portal por HTTPS. Cookie Secure é obrigatório em APP_ENV=prod.
- Configurar o proxy para que `Request.client.host` represente o IP real, confiando SOMENTE no endereço/CIDR do proxy autorizado (`FORWARDED_ALLOW_IPS` do uvicorn). Sem isso, clientes atrás do mesmo proxy compartilham o limite. Nunca confiar em X-Forwarded-For arbitrário. O backend deve permanecer na rede interna; não ampliar confiança a hosts externos.
- Configurar o adaptador oficial de guias/acordos descrito abaixo. Isso ainda exige documentação e homologação do fornecedor tributário. A integração SIL encontrada no projeto é de consulta/carga; não há endpoint de emissão contratado neste código-base.

## Guias e acordos: contrato do adaptador

`PORTAL_TRIBUTOS_URL` é um endereço HTTPS configurado pelo administrador; `PORTAL_TRIBUTOS_TOKEN` é a credencial de serviço, somente no backend. As chamadas não seguem redirecionamento. O adaptador deve validar a titularidade do CPF e recalcular valores no sistema oficial, inclusive pagamento já realizado, juros e descontos.

- `POST {URL}/acordos`: recebe `{cpf}`; responde `{acordos:[{id,descricao,situacao,valor,vencimento}]}`. Datas ISO, valor numérico finito não negativo. Retorna apenas os acordos daquele CPF.
- `POST {URL}/guias`: recebe `{cpf,tipo,id}`. Para `lancamento`, o ID é `silver.lancamentos.chave_origem`; para `acordo`, é um ID previamente retornado para o CPF. Responde `{url:"https://host-oficial/..."}`.
- `PORTAL_GUIAS_HOSTS`: lista explícita de hosts exatos permitidos para as guias, separados por vírgula. URLs sem HTTPS, com credenciais ou fora da lista são recusadas.

O backend confere propriedade do lançamento e recusa itens quitados antes da emissão. O adaptador deve repetir a conferência na base oficial atualizada. Não se calcula boleto/PIX a partir do valor importado. Não se marca parcela como paga ao emitir ou abrir uma guia. Pagamento efetivo ocorre no documento/serviço oficial; conciliação continua pela origem tributária. Não há criação de novo acordo ou assinatura de renegociação nesta entrega.

Sem o adaptador configurado, as telas mostram indisponibilidade de emissão/consulta de acordos. A interface está pronta para consumir esse contrato, mas pagamentos reais não estão habilitados. Os valores de consulta indicam que vêm da última base importada; lista limitada a 300 lançamentos recentes, com aviso explícito quando atinge o teto.

## Verificação realizada

29 testes isolados de backend: plano, relacionamentos, tarefas, autorização, concorrência/versionamento, histórico, identificação, código de uso único, expiração/tentativas, limite por CPF, sessão, CSRF, titularidade e bloqueio de URLs de guias não autorizadas. 252 testes de frontend passaram; TypeScript, ESLint, Ruff e build Vite passaram. Fluxos de ação → projeto → tarefa e portal em viewport de 390px foram verificados no navegador. A demonstração não apresentou rolagem horizontal da página.

A suíte completa do backend PostgreSQL, migrações executadas e smoke Docker não foram rodados localmente por ausência desses serviços. O CI existente usa PostgreSQL e Docker; recebeu também etapa específica para `tests_unit`. A prévia local usa SQLite e cadastros sintéticos. Nenhuma migração, envio de e-mail, guia ou pagamento ocorreu no ambiente oficial.

## Teste sem código de e-mail ou celular

Entre primeiro no SIGPMF com perfil **admin**, no mesmo navegador e domínio, e abra **Serviços ao cidadão → Portal do Cidadão**. A entrada mostrará **Acesso de teste · administrador SIGPMF** e o botão **Entrar para testar, sem código**. Informe CPF, nascimento e nome completo da mãe conforme as bases internas. Esse fluxo não depende de SMTP ou de contato cadastrado.

O endpoint `POST /api/portal/acesso-teste` exige o JWT ativo de um administrador do SIGPMF e confere os três dados cadastrais. Cria sessão de 15 minutos identificada como teste, com a mesma proteção de cookie, CSRF e titularidade, e registra `portal_acesso_teste` com o administrador autorizador. O modo aparece em um aviso persistente na área autenticada. Usuários anônimos e outros perfis não podem dispensar o código; o formulário público segue com confirmação por contato. Não é preciso alterar APP_ENV ou desativar proteções do restante da aplicação.
