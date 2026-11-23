# Plano de Governo no SIGPMF

Módulo integrado em `/plano-governo`, usando shell, sessão JWT, perfis, vínculos vigentes de secretaria, auditoria e PostgreSQL existentes. Não cria uma aplicação, conta ou banco paralelo.

## Uso

A visão do prefeito resume a carteira, a cobertura dos oito eixos e a documentação recente. O quadro permite filtrar, alterar situação e alternar para Kanban. Cada compromisso aceita vários projetos; um projeto pode contribuir para vários compromissos. O projeto deve ter secretaria ou responsável individual e pode conter prazo, orçamento informado, próxima ação, impedimento, tarefas com prazo e responsável próprio (ou herdado do projeto), pesos das entregas e evidências.

Cada gravação de projeto preserva uma observação de gestão com data, autor, situação, avanço, prazo, valores e marcos. A evolução semanal compara a última observação disponível até cada data; períodos sem medição permanecem sem medição. O gráfico documental conta URLs distintas por semana e não mede execução física. A conclusão de um projeto exige 100%, todos os marcos concluídos (se houver) e evidência. A validação do compromisso é uma ação separada, com justificativa e fonte; reabertura ou perda dos projetos de suporte exige revisão.

## Base inicial

70 compromissos transcritos do plano, com página original, pesquisa documental e vínculo com TI. 49 iniciativas organizam os itens com entrega, ampliação ou etapas documentadas. Há 74 vínculos documentais e 46 URLs distintas nessas iniciativas. A pesquisa identifica 13 itens como entrega comprovada na fonte; todos permanecem sujeitos à validação oficial. As demais propostas e suas referências continuam consultáveis na tela de compromissos.

Não foram atribuídos responsáveis, datas contratuais ou percentuais físicos fictícios. Fontes sem dia exato ficam fora dos gráficos semanais. Notícias de ações futuras e escopo parcial mantêm suas ressalvas. A carga inicial aparece na primeira leitura e é persistida junto com a primeira alteração. A atualização documental é idempotente e preserva registros existentes.

## Acesso

- `admin`: toda a carteira, projetos, responsáveis e validação.
- `operador` com vínculo vigente `TODAS`: gabinete, visão global e validação; não altera execução dos projetos.
- `operador` com secretaria: projetos e compromissos do seu escopo; também projetos individualmente atribuídos ao seu e-mail.
- `leitura` e `admin_lgpd`: consulta no escopo vigente, inclusive quando existe vínculo `TODAS`; esse vínculo não amplia o perfil para escrita.
- Sem vínculo ou atribuição: acesso negado.

Secretarias vêm de `controle.secretarias`. Eixos do plano não são automaticamente novas secretarias. Se um código proposto pela pesquisa não existir no catálogo, o vínculo fica a definir pelo administrador. Responsáveis/pontos focais não criam contas nem concedem acesso. A TI continua gerenciando contas e vínculos na tela existente.

## Ativação

1. Publicar backend e frontend desta revisão pelo processo normal do SIGPMF.
2. Aplicar `alembic upgrade head` no backend: a revisão aditiva `0049` cria `controle.plano_governo_estado`.
3. Abrir **Gestão → Plano de Governo** com administrador, revisar os vínculos de secretarias, cadastrar os pontos focais e salvar a primeira atualização.
4. Cada secretaria confirma escopo, responsável, prazo e primeira medição. O gabinete revisa os critérios antes de validar compromissos.

Nenhuma migração ou gravação foi executada no banco oficial durante o desenvolvimento. Um downgrade remove a tabela do módulo e seus dados: preservar backup antes de qualquer reversão de banco.

## Arquitetura e verificação

O plano 2025–2028 usa um agregado JSONB versionado. As relações são validadas no domínio; compare-and-swap retorna HTTP 409 quando há uma gravação concorrente e preserva a edição no formulário. A auditoria é gravada na mesma transação. As respostas são filtradas no servidor; o navegador não recebe projetos, históricos ou responsáveis de outras secretarias. A evolução utiliza registros imutáveis pelas ações da aplicação. O backup do PostgreSQL inclui o módulo; o exportador JSON é uma cópia de consulta do escopo visível.

Essa escolha atende uma carteira municipal pequena. Uma expansão para vários planos, grande concorrência ou BI SQL detalhado deve normalizar projetos e observações em tabelas próprias; atualmente alterações simultâneas em projetos distintos também podem exigir recarga.

Testes: `PYTHONPATH=. pytest -q tests_unit` no backend, `ruff check .`, testes Vitest, TypeScript e build Vite no frontend. O CI mantém a suíte PostgreSQL e o smoke Docker existentes, acrescidos dos testes isolados do módulo. A prévia local usa banco sintético SQLite para verificar o fluxo HTTP, sem tocar dados oficiais. A migração PostgreSQL e a suíte completa de backend precisam ser executadas pelo CI/ambiente com PostgreSQL; não há servidor PostgreSQL local nesta estação.

A revisão seguinte, 0050, e o portal público estão descritos em [portal-cidadao.md](portal-cidadao.md).
