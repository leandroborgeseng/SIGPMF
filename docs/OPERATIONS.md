# Operação

## Variáveis (`.env`, a partir do `.env.example`)
| Variável | Uso |
|---|---|
| APP_ENV | `dev`/`test`/`prod`. **Ausente = `prod`.** Docs só em dev. Carga pela tela (`POST /api/conectores/{id}/executar` e `POST /api/conectores/demo/executar`) só com `dev` ou `test`; fora disso as rotas respondem 404 |
| DOCS_HABILITADO | `true` liga `/api/docs` (só com APP_ENV=dev) |
| HTTP_PORT | porta publicada do frontend |
| POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD | banco interno (sem porta no host) |
| JWT_SECRET / JWT_EXPIRA_MINUTOS | assinatura e validade do token |
| ADMIN_EMAIL / ADMIN_PASSWORD | primeiro admin (criado só se não houver usuários) |
| CPF_REVEAL_PERFIS | perfis que revelam CPF (padrão `admin_lgpd`) |
| REVELAR_MOTIVO_MIN / REVELAR_CPF_SEGUNDOS | motivo mínimo (10) e tempo até remascarar (60 s) |
| AMOSTRA_LIMITE / LOTE_TAMANHO | linhas por tabela na amostra (100) e tamanho do lote |
| SQLSERVER_HOST/PORT/DATABASE/USER/PASSWORD/TIMEOUT | origem SQL Server (somente leitura) |
| SQLSERVER_DATABASE_IPTU / _DIVIDA / _FINANCEIRO | banco de origem de cada conector tributário (vazio = SQLSERVER_DATABASE) |
| FINANCEIRO_EXERCICIO_MIN | `Financeiro_Exercicio` só a partir deste exercício (vazio = atual e anterior); o recorte entra em toda consulta, contagem e conferência |
| AGENDA_HABILITADA | `true` liga a agenda das cargas completas no backend (padrão `false`; nunca roda com `APP_ENV=test`) |
| AGENDA_JANELA | horário em que a agenda pode **iniciar** cargas, `HH:MM-HH:MM` no fuso da agenda (padrão `00:00-06:00`; `00:00-24:00` = o dia todo; `22:00-06:00` atravessa a meia-noite). Uma carga iniciada na janela termina mesmo depois dela |
| SIGPMF_EDUCACAO_JANELA_NOTURNA | `true` (padrão): `POST /api/educacao/conector/completa` só inicia dentro da `AGENDA_JANELA`, como antes. Vale para a carga manual (`POST /api/educacao/conector/completa`), para a agenda da educação e para o "Executar agora" dela. Só `false` explícito (sem diferenciar maiúsculas, espaços nas pontas ignorados) desliga; ausente, vazio ou qualquer outro valor mantém ligada. `false`: a janela deixa de bloquear a carga completa da educação; amostra com sucesso, perfil ADMIN e auditoria continuam obrigatórios, lote e pausa não mudam, e a execução grava no log (WARNING) e na auditoria (`janela_ignorada: true`, `fora_da_janela`) que a janela foi ignorada e se a carga rodou fora dela. Em qualquer modo, uma segunda carga completa com outra `em_andamento` recebe 409. Não afeta o SEI. Para reverter, volte para `true` |
| SIGPMF_EDUCACAO_ORFA_MIN | minutos sem batida (padrão `30`) para o ADMIN poder marcar como `falha` uma carga completa da educação presa em `em_andamento`. A carga atualiza `execucoes.atualizado_em` a cada lote. `POST /api/educacao/conector/execucoes/{id}/marcar-falha` (id numérico, só ADMIN, auditado) faz um UPDATE atômico com WHERE em status e batida; 409 se a batida é recente ou se não está em andamento. `POST /api/educacao/conector/cancelar` (só ADMIN, auditado; 409 sem carga em andamento) pede o cancelamento: a carga para no fim do lote atual com status `cancelada`, mantém o que já gravou e o checkpoint. Depois de qualquer um dos dois, uma nova carga completa pode começar. **Atenção:** `/cancelar` numa execução órfã (processo morto, ainda `em_andamento`) devolve 200 e só grava o pedido, sem efeito, porque não há processo para ler o pedido. Nesse caso use `marcar-falha` (depois de N min sem batida) ou `POST /api/conectores/educacao/execucoes/{id}/interromper` (sem processo vivo) |
| SIGPMF_GESTAOESCOLAR_PAUSA_LOTE_MS | pausa entre lotes da carga completa da educação, em ms (padrão `0` = sem pausa, igual ao comportamento anterior). Vazio, negativo ou inválido vira `0` com WARNING no log, e a carga roda normalmente. A pausa dorme em fatias de 100 ms checando o cancelamento (o `/cancelar` tem efeito durante a pausa) e grava a batida antes, a cada ~1 s e depois, então uma pausa longa não faz a execução parecer órfã. Não pausa depois do último lote. Sugestão para carga em horário comercial: `2000` |
| (educação) trava e órfãs | No máximo uma carga completa da educação `em_andamento`: o índice único parcial `uq_execucoes_educacao_completa_em_andamento` (migração 0025) recusa a segunda com 409 auditado, e a carga segura o advisory lock do sistema `GESTAOESCOLAR` (mesma chave da CC12) enquanto roda. Por isso, no startup a rotina `interromper_orfas` também marca como `interrompida` a completa da educação sem processo vivo, e `POST /api/conectores/educacao/execucoes/{id}/interromper` (ADMIN, motivo obrigatório, auditado) atende a educação com as mesmas regras dos outros conectores. A batida (`atualizado_em`) é gravada a cada lote, durante a pausa e a cada 30 s na etapa final (prata). O fim da carga só grava o status se a execução ainda está `em_andamento`, sem sobrescrever uma interrompida, cancelada ou marcada como falha |
| AGENDA_FUSO | fuso dos horários (padrão `America/Sao_Paulo`) |
| AGENDA_INTERVALO_S | de quanto em quanto tempo a agenda confere se há carga a iniciar (padrão 60 s) |
| AGENDA_PULAR_SEM_MUDANCA | `true` (padrão): antes de ler, compara `COUNT(*)` e `MAX(campo incremental)` de cada tabela com a última carga bem-sucedida; igual = "Sem mudança na origem", nada é lido nem reconstruído |
| AGENDA_TENTATIVAS / AGENDA_REESPERA_MIN | falha tenta de novo depois de `REESPERA_MIN` (30) até `TENTATIVAS` (3) vezes no dia |
| AGENDA_COMPLETA_DIAS | padrão **0**: a **primeira** execução agendada de cada conector (sem `ultima_completa_em`) é **completa**; depois a agenda faz **só incremental** e nunca outra completa (outras completas só manualmente por ADMIN, auditadas). Com N > 0 volta o modo antigo: completa a cada N dias e na primeira vez (máx. 90), **exceto a educação, que na agenda roda sempre incremental, mesmo com N > 0** (a completa da educação é só manual, por ADMIN) |
| CARGA_INCREMENTAL_MARGEM_H | a incremental lê desde a maior data de alteração já gravada na bronze menos esta margem (padrão 24 h) |
| AGENDA_ESPERA_MAX_H | quanto a agenda espera uma carga terminar antes de seguir (padrão 20 h) |
| SMTP_HOST / SMTP_PORT / SMTP_USUARIO / SMTP_SENHA / SMTP_STARTTLS / ALERTA_DE / ALERTA_PARA | e-mail de alerta quando uma carga da agenda falha (opcional; sem `SMTP_HOST` só fica na tela e na auditoria) |

## Backup e restore
```bash
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > backups/sigpmf-$(date +%F).dump
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < backups/ARQUIVO.dump
```
`backups/` está no `.gitignore`. O volume `pgdata` persiste entre `down`/`up` (não use `down -v` em produção).

## Operação
- Saúde: `curl http://localhost:$HTTP_PORT/api/health` → `{"status":"ok","db":"ok","versao":...}` (503 se o banco cair).
- Logs: `docker compose logs -f backend` (CPF nunca é logado).
- Execuções: tela Conectores ou `controle.execucoes`/`controle.logs`.
- Uma carga que falha no meio fica `falha`; reexecutar retoma sem duplicar (lotes confirmados, dedupe por hash).
- Publicação (Coolify/Railway) só com OK do Leandro.

## Agenda das cargas (AG1–AG6)
- Tela **Conectores → Agenda das cargas**: horário de cada conector, ligar/desligar, próxima execução, última carga (com o
  motivo) e linha do tempo das últimas 24 h. Todos os perfis veem; só `admin` altera e usa **Executar agora** (auditado).
- A agenda usa a mesma carga completa da tela: mesma trava por conector, checkpoint, pausa entre lotes, limite de disco e
  auditoria (usuário `agenda`). **Uma carga por vez**, na ordem da tabela; a próxima só começa quando a anterior termina.
- Só repete cargas já liberadas: o conector precisa de uma amostra com sucesso (senão fica "Não liberada").
- Homologação: `AGENDA_HABILITADA=true` e `AGENDA_JANELA=00:00-24:00`. Produção: `AGENDA_JANELA=00:00-06:00`.
- Só um processo agenda (trava `agenda:lider`); com mais de um worker, os outros ficam de reserva.

## Contagens automáticas (PR-07, J1–J4)
- Agenda: item `contagens`, **05:45** no fuso da agenda (`AGENDA_FUSO`, padrão America/Sao_Paulo), depois das cargas
  da noite e dentro da `AGENDA_JANELA` de produção (`00:00-06:00`). Nasce **desligado** (migração 0034), como os
  demais; ligar pela tela da agenda (admin, auditado). Também roda por `POST /api/contagens/executar` (admin).
- Login: só `SQLSERVER_CONTAGENS_USER` / `SQLSERVER_CONTAGENS_PASSWORD` (permissão mínima, só SELECT). Sem eles a
  execução fica `recusada` (`login_contagens_ausente`); nunca usa o `SQLSERVER_USER`. Login com escrita/DDL/admin é
  recusado por banco (`login_nao_minimo`).
- Grava só agregados (`controle.contagem_resultado`, com hash do SQL). Falhas com código fixo (`base_inacessivel`,
  `consulta_falhou`, `sql_recusado`, `resultado_nao_agregado`); um banco com falha não para os outros.
- Consultas com nome de tabela a confirmar (Q1, Q5, Q6, Q8, Q11) ficam desligadas até o DBA confirmar:
  `CONTAGENS_ATIVAS=Q1,Q11,...` liga só as listadas. Q2–Q4 foram removidas (leem o Geral, catalogado "não ler").
- J4: CPFs repetidos só com `CONTAGENS_CPF_REPETIDO_LIMIAR` (padrão 50) ou mais ocorrências, no máximo
  `CONTAGENS_CPF_REPETIDO_TOP` (padrão 100) por tabela. Leitura só `admin_lgpd`
  (`GET /api/contagens/{id}/cpfs-repetidos`), auditada.

## Variáveis numéricas no Coolify (nota do Tester, #45)
- **Nunca crie uma variável numérica vazia** no Coolify (porta, limite, timeout, limiar…). Valor vazio não vira o
  padrão: a validação falha e o backend **não sobe** (ex.: `SIGPMF_RELEITURA_SAUDE_PORTA` vazia impediu a subida).
  Dê um valor ou não crie a variável.

## Carga incremental e camadas (IN1–IN4)
- **Incremental**: por tabela, lê só as linhas com `campo incremental >= marca d'água` (maior data já gravada na bronze
  − `CARGA_INCREMENTAL_MARGEM_H`). Tabela sem campo incremental na política, ou sem histórico, é lida por inteiro.
  Não verifica de novo a unicidade da chave nem atualiza checkpoints (isso é da completa). Exclusões na origem só
  aparecem numa completa manual (ADMIN) enquanto a conferência leve por PK/hash (IN5/IN7) não entra.
- **Saúde**: `Unidades`, `Aux_Vacina` e `Aux_Especialidades` não têm coluna de data e são relidas inteiras em toda
  incremental (dimensões pequenas; hash evita gravar o que não mudou).
- **SIL**: a API não tem data; toda execução lê tudo e a bronze só grava a linha cujo hash mudou (exceção ao IN2).
- **Educação** (`POST /api/educacao/conector/incremental`, e é o que a agenda roda): cada fonte com `*_dataAlteracao`
  lê só `>= marca d'água − CARGA_INCREMENTAL_MARGEM_H`; a marca fica em `controle.checkpoints_carga.marca_dagua`
  (0027) e **não é zerada** no fim do ciclo (só o keyset volta ao início). Sem marca, parte da maior data já gravada
  na bronze. `PES_PessoaEndereco` também entra quando `END_Endereco.end_dataAlteracao` mudou; pessoas do CoreSSO
  entram também quando o aluno/responsável ligado a elas mudou (entrada no escopo). `ACA_TipoResponsavelAluno`
  (8 linhas, sem data lida) é relida inteira. Sem linha nova ou alterada: "Concluída · nenhuma linha nova" e a
  prata/ouro não são refeitas. O log de cada fonte traz "incremental desde <data>; N lidas, X novas, Y alteradas".
  A completa (`/completa`) segue só manual, por ADMIN. Na agenda a educação roda **sempre incremental**, mesmo com
  `AGENDA_COMPLETA_DIAS` > 0 (essa variável não vale para a educação).
- **Prata e ouro só quando algo mudou**: antes de reconstruir, compara a assinatura das entradas (bronze: total, maior
  id e quarentena; confirmações de óbito; parâmetros da correlação; hash do código das regras) com a da última
  reconstrução bem-sucedida (`controle.camadas_estado`). Igual = reconstrução pulada ("Nada mudou nas entradas da prata
  e do ouro"). A reconciliação da carga completa roda do mesmo jeito. "Retomar prata e ouro" sempre reconstrói.
- Um deploy que muda as regras (correlação, mapeamentos, ouro) muda o hash e força a próxima reconstrução.

## Tributário (onda 2, TR1–TR8)
- Conectores `iptu` (Cadastro, Cadastro_Possuidor), `divida` (Divida) e `financeiro` (Financeiro_Exercicio, com recorte
  de exercício). Mesma rotina das outras bases: amostra com sucesso → carga completa → agenda.
- São **fatos**: não entram na correlação de pessoas e nunca criam cidadão. A prata tributária (`silver.imoveis`,
  `imovel_possuidores`, `lancamentos`, `dividas`) liga o fato ao cidadão só pelo CPF já correlacionado; o lançamento e a
  dívida vão para o possuidor principal ativo do imóvel. PJ e CPF sem cidadão ficam só com o documento.
- Ficha: `GET /api/gold/cidadaos/{id}/tributario` (todos os perfis autenticados, auditado como `consulta_tributario`).

## Saúde (fatos), linha do tempo e ouro agregado (onda 3, SA-F1..SA-F6)
- Saúde passa a trazer, **sem conteúdo clínico**, `Consultas` (12 meses), `Agendamento_Consulta` (12 meses), `Vacina`
  (24 meses) e as dimensões `Unidades`, `Aux_Vacina`, `Aux_Especialidades`. Queixa, HMA, suspeita, exame físico, sinais
  vitais, lote e observações estão na lista de colunas proibidas.
- São fatos: não passam pela correlação de pessoas; ligam ao cidadão pela matrícula do paciente já correlacionada.
  ❓ confirmar com a Saúde que `Consultas.Cod_Paciente` e `Agendamento_Consulta.Cod_Pac` são a `Matricula`.
- Linha do tempo: `silver.eventos` (consulta, agendamento, vacina, posse de imóvel, pagamento, parcela vencida,
  inscrição em dívida). API `GET /api/gold/cidadaos/{id}/linha-do-tempo?limite=&antes=AAAA-MM-DD` (auditada).
- Saúde do cidadão: `GET /api/gold/cidadaos/{id}/saude` (próximos agendamentos, atendimentos, vacinas; auditada).
- Ouro agregado: `gold.indicadores` (série, período, dimensão, valor; sem pessoa; grupos de saúde com menos de 5 casos
  suprimidos). API `GET /api/indicadores?series=kpi,saude_atend_mes,...` para os painéis.

## Explorador de vínculos (onda 6, VN1–VN5)
- Tela **Dados → Explorador de vínculos** (só `admin` e `admin_lgpd`): parte de uma pessoa e abre, um nível por clique,
  imóveis → outros possuidores (pessoas e empresas) → os imóveis deles, até 3 níveis.
- `GET /api/vinculos/{no}?motivo=&processo=&nivel=`: motivo obrigatório (mínimo `REVELAR_MOTIVO_MIN`); ids opacos
  (`pf:` cidadão, `im:` imóvel, `ps:` possuidor sem cidadão) — CPF/CNPJ nunca vão na URL e saem mascarados; cada
  abertura auditada como `consulta_vinculos` (alvo = cidadão quando é pessoa).
- Sócios de empresas (ISS) e família (Senior/CadÚnico) entram como novos tipos de vínculo quando esses conectores forem ligados.

## Políticas de dados e retenção (onda 7, PL1–PL4)
- Tela **Dados → Políticas de dados**: regras gerais, conjuntos (classificação, base legal, retenção, tratamento, quem vê),
  matriz perfis × áreas e a limpeza automática. Configuração versionada em `app/catalogo/politicas_dados.py` (muda por PR).
- Retenção (`app/services/retencao.py`): uma vez por dia, depois das cargas da agenda (ou **Executar agora**, admin,
  auditado). Apaga só **versões antigas** da bronze (com versão mais nova e extraídas há mais de
  `RETENCAO_BRONZE_VERSOES_DIAS`, padrão 7) e logs técnicos com mais de `RETENCAO_LOGS_DIAS` (90). Nunca apaga a versão
  atual de um registro, linha citada pela prata, quarentena nem a auditoria. Cada execução fica em `controle.execucoes`
  (tipo `retencao`).

## Reset da SIL (recomeçar do zero) — `backend/scripts/reset_sil.py`

Pedido do Leandro em 29/10/2026. Só o DevOps roda, e só depois do OK do Leandro no privado. O script **não chama a
API SIL** e não toca em Saúde, educação, SEI nem nas outras fontes. A `controle.auditoria` nunca é apagada: o reset
grava nela uma linha `acao='reset_sil'` (operador, modo, contagens por tabela antes/depois e o schema de backup).

**Escopo (exatamente estas tabelas e filtros):**

| Tabela | Linhas | Ação |
|---|---|---|
| `bronze.registros` | `upper(sistema_origem)='SIL'` (inclui quarentena e as linhas `sandbox`) | apaga |
| `silver.empresas_sil` | todas (prata exclusiva da SIL) | apaga |
| `silver.vinculos`, `silver.correlacoes_pendentes`, `silver.inconsistencias` | `upper(sistema)='SIL'` (a SIL é "fato"; esperado 0) | apaga |
| `gold.cidadao_dados` | `upper(sistema)='SIL'` de cidadãos que não são "só SIL" | apaga |
| `controle.logs` | das execuções da SIL | apaga |
| `controle.checkpoints_carga` | `lower(sistema)='sil'`, produção e o antigo `sandbox` | apaga (a próxima carga recria com `posicao=0`, ou seja, `Sequencia=1`) |
| `gold.cidadao_sistemas` | cidadãos que ficam no ouro e têm `"SIL"` na lista (menos o "só SIL" mantido sem a flag) | recalcula `sistemas`: a lista anterior sem `"SIL"` mais as fontes dos dados que ficam em `gold.cidadao_dados` (ordenada). Quem tem dado de outra fonte nunca fica com `[]` (ex.: `["SIL"]` + óbito do cemitério vira `["CEMITERIO"]`) |
| `controle.camadas_estado` | as que apontam para uma execução da SIL | atualiza `execucao_id=NULL` |
| `controle.agendamentos` | `conector='sil'` | volta ao estado inicial, **`ativo=false`** (horário mantido) |
| `controle.execucoes` | `lower(sistema)='sil'` | apaga |
| `gold.cidadao_dados` + `gold.cidadao_sistemas` | **só com `--remover-ouro-so-sil`**: cidadão cuja única fonte é a SIL (`sistemas=["SIL"]` e nenhum dado do ouro de outra fonte) | apaga |

Fica de fora: API SIL, outras fontes, `controle.auditoria`, `silver.cidadaos`. Sem a flag, o cidadão "só SIL"
continua no ouro com `sistemas=["SIL"]` até a próxima reconstrução do ouro.

**Proteções:** uma transação só. Logo depois do BEGIN pega `pg_try_advisory_xact_lock` na mesma chave das cargas da
SIL (`crc32('amostra:SIL')`, a de `app.services.trava.chave_lock`, da carga completa e da `amostra_sil`): se uma carga
estiver rodando, recusa (saída 2) sem mudar nada; e nenhuma carga SIL começa enquanto o reset não termina. Ligar a
agenda da SIL (`PUT /api/agenda/sil` com `ativo=true`) tenta a mesma trava antes de gravar: ocupada (carga ou reset
em andamento) → 409 com mensagem fixa, `ativo` continua `false` e a recusa vai para a auditoria
(`agenda_alterada_recusada`, `motivo=sil_trava_ocupada`). Desligar ou mudar o horário não depende da trava. A
conferência da auditoria é "nenhuma linha anterior sumiu" (outras sessões podem auditar em paralelo). Recusa
também (saída 2) se a agenda da SIL estiver `ativo=true`, se houver execução SIL
`em_andamento`, se faltar `--usuario` ou se o schema de backup já existir com tabelas. Confere que nada da SIL sobrou
e que as contagens das outras fontes (e da auditoria) não mudaram; qualquer divergência dá ROLLBACK (saída 3). A
saída mostra só contagens.

**Backup (dentro da mesma transação, antes de apagar):** as linhas que vão sair/mudar são copiadas para o schema
`backup_sil_20261029` (`--schema-backup` troca o nome), uma tabela por alvo (`bronze__registros`,
`controle__execucoes`, ...). Cada cópia tem de bater com a contagem da origem, senão ROLLBACK. O schema fica com
`REVOKE ALL` de `PUBLIC` e de todos os outros papéis; só o dono (o `POSTGRES_USER`, único papel que o app usa) lê.
A simulação desfaz também o schema de backup. As cópias do ouro "só SIL" (`gold__cidadao_dados__so_sil`,
`gold__cidadao_sistemas__so_sil`) existem sempre (vazias sem a flag), e `gold__cidadao_sistemas__tira_sil` guarda a
lista de sistemas anterior. Depois do COMMIT o script relê `controle.agendamentos.ativo` e mostra o valor. Opcionalmente, antes de tudo, um dump extra fora do banco:

```bash
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc -t bronze.registros -t silver.empresas_sil \
  -t silver.vinculos -t silver.correlacoes_pendentes -t silver.inconsistencias -t gold.cidadao_dados \
  -t gold.cidadao_sistemas -t controle.logs -t controle.checkpoints_carga -t controle.camadas_estado \
  -t controle.agendamentos -t controle.execucoes "$POSTGRES_DB"' > backups/pre-reset-sil-$(date +%F).dump
```

**Rodar:**

```bash
docker compose exec backend python -m scripts.reset_sil --usuario "Nome do operador"              # simulação (ROLLBACK)
docker compose exec backend python -m scripts.reset_sil --usuario "Nome do operador" --confirmar  # aplica
# com a decisão do Leandro de tirar do ouro quem só existe pela SIL: acrescente --remover-ouro-so-sil
```

**Rollback** (devolve as linhas e o checkpoint/agenda anteriores; usa a mesma flag do reset):

```bash
docker compose exec backend python -m scripts.reset_sil --imprimir-rollback > rollback-sil.sql
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v operador="Nome"' < rollback-sil.sql
```

O SQL é `INSERT INTO <tabela> SELECT * FROM backup_sil_20261029.<cópia>` (execuções primeiro, por causa das FKs),
sempre incluindo todo o ouro que está no backup (com ou sem a flag no reset), depois `UPDATE` da agenda, da
`gold.cidadao_sistemas` (volta o `"SIL"`) e da `camadas_estado` com os valores do backup, e uma linha `reset_sil_rollback` na
auditoria, tudo com `ON_ERROR_STOP` numa transação. Só faz sentido antes de uma nova carga da SIL (senão há conflito
de chave e o psql para sem mudar nada).

**Limpeza do backup:** o script nunca apaga o schema de backup. Remover só manualmente, **com OK explícito do
Leandro e no mínimo 7 dias depois do reset**: conferir a primeira carga nova, registrar na auditoria e então
`DROP SCHEMA backup_sil_20261029 CASCADE;`.

## Educação: documentos (PES_PessoaDocumento) e releitura de uma fonte só

**Tipos de documento.** A carga lê primeiro o catálogo `FRA_CoreSSO.dbo.SYS_TipoDocumentacao`, que tem 7 linhas e
nenhum dado pessoal. Um tipo é aceito quando `tdo_classificacao` está em (1, 3, 4), que são CPF, PIS e NIS, ou quando a
sigla é `CPF`, `NIS`, `PIS` ou `PIS/PASEP`. A sigla é comparada sem diferenciar maiúsculas e sem os espaços das pontas. Qualquer
outra classificação é ignorada: na origem, os 7 tipos estão com 99. CNS (saúde, fora da v1), CIN, CNPJ, RG e sigla vazia ficam de fora.
Os `tdo_id` aceitos entram como **parâmetros** em `d.tdo_id IN (...)`, e cada linha recebe a classe derivada em
`tdo_classe`. Se o catálogo não tiver nenhum tipo aceito, a carga (e a amostra) termina em `falha` com o código
`sem_tipo_documento` e uma mensagem fixa, sem o texto da exceção. CPF e NIS/PIS vão para a prata da educação.

**Tabela vazia (todas as fontes).** Quando uma tabela termina a carga **completa** com 0 linhas lidas, a execução
continua `sucesso` (a agenda, a assinatura e a `ultima_completa_em` não mudam), mas:
- grava um log `WARN`/`WARNING` com `[tabela_vazia]` e o nome da tabela;
- recebe o código `tabela_vazia` (`erro_classe`) e as tabelas (`erro_tabela`);
- leva o aviso "Atenção: N tabela(s) terminaram a carga completa com 0 linhas lidas (...)" na mensagem;
- mostra o campo `alerta` na API do card (`GET /api/conectores/{c}/completa`), na última execução e na agenda;
- dispara e-mail pela agenda quando `SMTP_HOST` e `ALERTA_PARA` estão configurados.

Vale para Saúde, Cemitério, IPTU, Dívida, Financeiro, SIL e educação. Na carga incremental, 0 linhas é normal e não
gera aviso.

**Reler só PES_PessoaDocumento, sem carga completa.** Esse procedimento não apaga nada da bronze.
1. Confira que não há carga completa da educação em andamento. Se houver, o script recusa com saída 2.
2. Dentro do container do backend:
   `python -m scripts.releitura_fonte_educacao --fonte PES_PessoaDocumento --usuario "Nome do operador"`
   O script só altera `controle.checkpoints_carga`:
   - a fonte volta ao início (posição 0, não concluída);
   - os checkpoints das outras 12 tabelas passam a `concluída`, com a posição igual à contagem de linhas que cada
     uma já tem na bronze. É assim que a carga pula essas 12 e lê só a fonte pedida;
   - o pedido é auditado (`educacao_releitura_fonte`).
3. Rode a carga completa da educação pela tela, pela agenda ou pelo "Executar agora". A janela noturna continua
   valendo. Ela lê **só** PES_PessoaDocumento e, ao fechar o ciclo, reconstrói a prata da educação a partir da bronze
   inteira, sem reler as outras 12 tabelas.
4. Confira no card que a execução ficou `sucesso`, sem o alerta `tabela_vazia`, e com as linhas lidas da tabela.
O mesmo vale para qualquer outra fonte da educação (`--fonte <nome>`).

**Releitura no deploy, sem terminal (`SIGPMF_RELEITURA_FONTE`).** Para quando não há terminal no container. É de uso
único e não fica no Dockerfile: é uma variável de ambiente que você define no Coolify só para um deploy.
1. Antes, **desligue a agenda da educação** (senão a releitura recusa com `agenda_ligada`) e confira que já existe uma
   amostra da educação com `sucesso`.
2. Defina `SIGPMF_RELEITURA_FONTE=PES_PessoaDocumento` (ou outra fonte do catálogo da educação) e faça o deploy no
   SHA aprovado. **Marcador = fonte + SHA do deploy.** O SHA vem de `SOURCE_COMMIT` (o Coolify injeta; o
   `docker-compose.yml` declara no serviço backend; vazio = ausente), com fallback para `GIT_SHA` e depois `SIGPMF_BUILD_SHA` (só hex de 7 a 40
   caracteres). Sem SHA válido, recusa com `sem_sha_deploy` (não lê nada) em vez de arriscar repetir.
3. O boot não espera: a app sobe e o `/health` responde normalmente. Uma thread em segundo plano espera o `/health`
   da própria app dar 200 em `http://127.0.0.1:<SIGPMF_RELEITURA_SAUDE_PORTA>/health` (porta padrão **8000**; se o
   backend escutar em outra porta, ajuste a variável, senão recusa `sem_saude`), até `SIGPMF_RELEITURA_SAUDE_MAX_S`
   (padrão 600 s; senão recusa `sem_saude`), e então:
   - pega a **mesma trava** da carga da educação (advisory lock do sistema) e a segura até o fim;
   - já com a trava, **confere o marcador de novo**: se outra instância no mesmo SHA já concluiu, não relê
     (log `INFO [releitura_deploy:ja_executada]`). Assim duas instâncias subindo com o mesmo SHA não rodam duas vezes;
   - roda a mesma lógica de `scripts.releitura_fonte_educacao` com o usuário `sistema-deploy` (auditado em
     `educacao_releitura_fonte`) e lê só essa fonte. A janela noturna continua valendo (`SIGPMF_EDUCACAO_JANELA_NOTURNA`);
   - ao fechar o ciclo, reprocessa a prata da educação e o vínculo com o cidadão (ouro), como a carga completa;
   - grava a auditoria `educacao_releitura_deploy` e o marcador `concluida` em `controle.acoes_unicas` (fonte + SHA).
4. Recusas, sempre com código fixo e sem texto de exceção: `fonte_desconhecida` (não lê nada), `agenda_ligada`,
   `sem_amostra`, `fora_janela`, `trava_ocupada`, `em_andamento`, `sem_credencial`, `sem_saude`, `sem_sha_deploy`. Cada uma grava log
   `WARNING [releitura_deploy:<codigo>]`, a auditoria `educacao_releitura_deploy_recusada` e uma execução
   `releitura_deploy` com status `recusada` e campo `alerta` (código e texto) na última execução do painel.
   **Atenção (`fora_janela`):** a janela noturna da educação vale também aqui. Um deploy **de dia** com
   `SIGPMF_EDUCACAO_JANELA_NOTURNA` ligada recusa com `fora_janela` (nada é lido). Para reler de dia, desligue a janela
   antes do deploy ou faça o deploy dentro da janela; o próximo boot no mesmo SHA tenta de novo.
5. Acompanhe em `GET /api/educacao/releitura-deploy` (ADMIN): fonte, SHA (`token`), status (`concluida`, `falha`, `recusada`,
   `em_andamento`), código e execução.
6. **Repetição.** Reiniciar no mesmo SHA não relê (log `INFO [releitura_deploy:ja_executada]`). Falha, recusa ou
   queda no meio **não** marcam `concluida`: o próximo boot no mesmo SHA tenta de novo. Um deploy de SHA novo com a
   variável ainda definida relê de novo — por isso remova a variável logo depois.
7. **Depois de `concluida`, remova `SIGPMF_RELEITURA_FONTE` do Coolify** e religue a agenda
   da educação, se for o caso. Sem a variável, o boot não faz nada.

## Acesso por secretaria (PR-06, `SIGPMF_RBAC_SECRETARIA`)

- Tela de TI: menu **TI → Acesso por secretaria** (`/ti/acesso-secretaria`), só perfil `admin`. Busca por nome ou e-mail
  (o termo e o id do usuário vão só no corpo de POST, nunca na URL nem no log), vigência, quem cadastrou, remoção com
  motivo (lógica, nunca DELETE) e histórico vindo de `controle.auditoria`. Prefeito = vínculo "Todas as secretarias".
- Regra nas consultas nominais (ficha, saúde, impostos, educação, linha do tempo, listas de cidadãos): só com
  `SIGPMF_RBAC_SECRETARIA=true`. Desligada (padrão) = comportamento de antes. **Ordem para ligar em produção:**
  1) deploy com a variável ausente; 2) TI cadastra os vínculos de todos os usuários (inclusive o do prefeito);
  3) só então `SIGPMF_RBAC_SECRETARIA=true` e redeploy. Ligar antes corta o acesso de quem ainda não tem vínculo (403).
- Com a regra ligada, toda consulta liberada grava `controle.auditoria_acesso` (somente inserção) e toda recusa grava
  `acesso_secretaria_negado` em `controle.auditoria` (sem PII). Vínculo vencido, futuro ou removido = 403 na hora.
- Secretarias e mapa domínio → secretaria ficam nas tabelas `controle.secretarias` e `controle.dominio_secretaria`
  (seed na 0033: EDUCACAO, SAUDE, FAZENDA, ASSISTENCIA_SOCIAL, ADMINISTRACAO, SERVICOS_URBANOS, TODAS; saúde → SAUDE,
  tributário/impostos → FAZENDA, educação → EDUCACAO, **cemitério → SERVICOS_URBANOS com `a_confirmar=true`**).
  A TI (admin) ajusta sem deploy por `POST /api/ti/acesso-secretaria/referencia/secretaria|dominio` (upsert auditado,
  sem DELETE; desativar = `ativo=false`). Vínculo novo só aceita secretaria ativa da tabela. Domínio sem dono = qualquer
  vínculo vigente.
- Com vínculo vigente o dado nominal sai **sem máscara, inclusive o CPF completo** (TL 29/10), e a linha de
  `controle.auditoria_acesso` marca `revelou_cpf`. Admin sem vínculo recebe 403 (confirmado).
- **Menores só para a Educação; dado clínico (rota da Saúde) só para a Saúde** (prefeito vê tudo, com auditoria).
- A regra libera só pela secretaria **DONA** do domínio: vínculo com secretaria que não é dona de nenhum domínio do
  cidadão = 403 (ficha, linha do tempo, /sistemas, acessos); listas e CSV mostram só cidadãos com domínio da dona e
  escondem menores para quem não é Educação. Domínios do seed além dos da ficha (decisão do TL 29/10):
  `qualidade_dados` (pendências, inconsistências, confirmar óbito) e `explorador` → TODAS (prefeito); `sei` →
  ADMINISTRACAO; cemitério → SERVICOS_URBANOS segue `a_confirmar`. Admin sem vínculo perde esses três.
- **admin_lgpd por função** nesses três (pendências/inconsistências, explorador, confirmar óbito), sem vínculo: motivo
  obrigatório no cabeçalho `X-SIGPMF-Motivo` (mínimo de 10 caracteres; nunca na URL). Sem motivo = 400 e tentativa
  auditada (`acesso_por_funcao_negado`); com motivo = `acesso_por_funcao` (com o motivo) + linha em
  `controle.auditoria_acesso` (secretaria `FUNCAO_ADMIN_LGPD`). Só vale com a regra ligada.
- Vínculos (QA #50 c2): ninguém concede vínculo a si mesmo (403, auditado, qualquer secretaria). `adicionar` recusa TODAS
  (422, auditado). O TODAS só sai por `POST /api/ti/acesso-secretaria/vinculos/conceder-prefeito`, exclusivo do
  `admin_lgpd`, com motivo e sem data de fim. Recusas auditadas: conceder a si mesmo, a conta de perfil admin/admin_lgpd,
  ou a quem já concedeu TODAS ao solicitante (concessão cruzada). A linha TODAS da referência não muda e nenhum domínio
  é apontado para TODAS pela API (só o seed). `/contagens/{eid}/cpfs-repetidos` (PR-07): admin_lgpd com motivo no
  cabeçalho quando a regra está ligada.
- `/gold/cidadaos/{cid}/sistemas` (e `sistemas` na ficha) sai **vazio para quem não é prefeito** até existir o mapa
  sistema → domínio.
- **Pós-MVP:** telas para ajustar `controle.secretarias`/`controle.dominio_secretaria` e para a remoção do vínculo
  TODAS (hoje só pela API, admin e admin_lgpd respectivamente). Antes de ligar a regra, as telas do explorador e de
  pendências precisam enviar `X-SIGPMF-Motivo` para o admin_lgpd.
  O teste `test_qa2_toda_rota_do_app_esta_classificada` falha se surgir rota nova sem classificação.
- `admin_lgpd` revela CPF, aluno e empresa **por função, sem vínculo**, sempre com motivo e auditoria (sem motivo =
  400 + `revelar_cpf_negado`). O vínculo TODAS não é editado nem encerrado por `editar`/`remover` (422).
- Vínculo do prefeito (TODAS): não sai pela tela de TI; só o perfil `admin_lgpd` remove, por
  `POST /api/ti/acesso-secretaria/vinculos/remover-prefeito`, com motivo (10+ caracteres), auditado.
