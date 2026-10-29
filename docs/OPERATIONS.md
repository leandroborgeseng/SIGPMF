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
| `gold.cidadao_sistemas` | cidadãos que ficam no ouro e têm `"SIL"` na lista (menos o "só SIL" mantido sem a flag) | atualiza `sistemas = sistemas - 'SIL'` |
| `controle.camadas_estado` | as que apontam para uma execução da SIL | atualiza `execucao_id=NULL` |
| `controle.agendamentos` | `conector='sil'` | volta ao estado inicial, **`ativo=false`** (horário mantido) |
| `controle.execucoes` | `lower(sistema)='sil'` | apaga |
| `gold.cidadao_dados` + `gold.cidadao_sistemas` | **só com `--remover-ouro-so-sil`**: cidadão cuja única fonte é a SIL (`sistemas=["SIL"]` e nenhum dado do ouro de outra fonte) | apaga |

Fica de fora: API SIL, outras fontes, `controle.auditoria`, `silver.cidadaos`. Sem a flag, o cidadão "só SIL"
continua no ouro com `sistemas=["SIL"]` até a próxima reconstrução do ouro.

**Proteções:** uma transação só. Logo depois do BEGIN pega `pg_try_advisory_xact_lock` na mesma chave das cargas da
SIL (`crc32('amostra:SIL')`, a de `app.services.trava.chave_lock`, da carga completa e da `amostra_sil`): se uma carga
estiver rodando, recusa (saída 2) sem mudar nada; e nenhuma carga SIL começa enquanto o reset não termina. Recusa
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
