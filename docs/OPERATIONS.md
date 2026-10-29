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
| SIGPMF_EDUCACAO_JANELA_NOTURNA | `true` (padrão): a carga completa da educação só inicia dentro da `AGENDA_JANELA`, como antes. Vale para os três caminhos: a rota manual `POST /api/educacao/conector/completa`, a agenda da educação e o "Executar agora". Só um `false` explícito desliga (sem diferenciar maiúsculas, com espaços aparados); ausente, vazio ou qualquer outro valor mantém ligada. `false`: a janela deixa de bloquear a carga completa da educação; amostra com sucesso, perfil ADMIN e auditoria continuam obrigatórios, lote e pausa não mudam, e a execução grava no log (WARNING) e na auditoria (`janela_ignorada: true`, `fora_da_janela`) que a janela foi ignorada e se a carga rodou fora dela. Em qualquer modo, uma segunda carga completa com outra `em_andamento` recebe 409. Não afeta o SEI, que mantém a própria janela. Para reverter, volte para `true` |
| SIGPMF_EDUCACAO_ORFA_MIN | minutos sem batida (padrão `30`) para o ADMIN poder marcar como `falha` uma carga completa da educação presa em `em_andamento`. A carga atualiza `execucoes.atualizado_em` a cada lote. `POST /api/educacao/conector/execucoes/{id}/marcar-falha` (id numérico, só ADMIN, auditado) faz um UPDATE atômico com WHERE em status e batida; 409 se a batida é recente ou se não está em andamento. `POST /api/educacao/conector/cancelar` (só ADMIN, auditado; 409 sem carga em andamento) pede o cancelamento: a carga para no fim do lote atual com status `cancelada`, mantém o que já gravou e o checkpoint. Depois de qualquer um dos dois, uma nova carga completa pode começar. **Atenção:** `/cancelar` numa execução órfã (processo morto, ainda `em_andamento`) devolve 200 e só grava o pedido, sem efeito, porque não há processo para ler o pedido. Nesse caso use `marcar-falha` (depois de N min sem batida) ou `POST /api/conectores/educacao/execucoes/{id}/interromper` (sem processo vivo) |
| SIGPMF_GESTAOESCOLAR_PAUSA_LOTE_MS | pausa entre lotes da carga completa da educação, em ms (padrão `0` = sem pausa, igual ao comportamento anterior). Vazio, negativo ou inválido vira `0` com WARNING no log, e a carga roda normalmente. A pausa dorme em fatias de 100 ms checando o cancelamento (o `/cancelar` tem efeito durante a pausa) e grava a batida antes, a cada ~1 s e depois, então uma pausa longa não faz a execução parecer órfã. Não pausa depois do último lote. Sugestão para carga em horário comercial: `2000` |
| (educação) trava e órfãs | No máximo uma carga completa da educação `em_andamento`: o índice único parcial `uq_execucoes_educacao_completa_em_andamento` (migração 0025) recusa a segunda com 409 auditado, e a carga segura o advisory lock do sistema `GESTAOESCOLAR` (mesma chave da CC12) enquanto roda. Por isso, no startup a rotina `interromper_orfas` também marca como `interrompida` a completa da educação sem processo vivo, e `POST /api/conectores/educacao/execucoes/{id}/interromper` (ADMIN, motivo obrigatório, auditado) atende a educação com as mesmas regras dos outros conectores. A batida (`atualizado_em`) é gravada a cada lote, durante a pausa e a cada 30 s na etapa final (prata). O fim da carga só grava o status se a execução ainda está `em_andamento`, sem sobrescrever uma interrompida, cancelada ou marcada como falha |
| AGENDA_FUSO | fuso dos horários (padrão `America/Sao_Paulo`) |
| AGENDA_INTERVALO_S | de quanto em quanto tempo a agenda confere se há carga a iniciar (padrão 60 s) |
| AGENDA_PULAR_SEM_MUDANCA | `true` (padrão): antes de ler, compara `COUNT(*)` e `MAX(campo incremental)` de cada tabela com a última carga bem-sucedida; igual = "Sem mudança na origem", nada é lido nem reconstruído |
| AGENDA_TENTATIVAS / AGENDA_REESPERA_MIN | falha tenta de novo depois de `REESPERA_MIN` (30) até `TENTATIVAS` (3) vezes no dia |
| AGENDA_COMPLETA_DIAS | a agenda faz carga **incremental** (só o alterado) e uma **completa** a cada N dias e na primeira vez (padrão 7); a completa pega registros excluídos na origem |
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

## Carga incremental e camadas (IN1–IN4)
- **Incremental**: por tabela, lê só as linhas com `campo incremental >= marca d'água` (maior data já gravada na bronze
  − `CARGA_INCREMENTAL_MARGEM_H`). Tabela sem campo incremental na política, ou sem histórico, é lida por inteiro.
  Não verifica de novo a unicidade da chave nem atualiza checkpoints (isso é da completa). Exclusões na origem só
  aparecem na completa (a cada `AGENDA_COMPLETA_DIAS`).
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
