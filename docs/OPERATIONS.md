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
| AGENDA_HABILITADA | `true` liga a agenda das cargas completas no backend (padrão `false`; nunca roda com `APP_ENV=test`) |
| AGENDA_JANELA | horário em que a agenda pode **iniciar** cargas, `HH:MM-HH:MM` no fuso da agenda (padrão `00:00-06:00`; `00:00-24:00` = o dia todo; `22:00-06:00` atravessa a meia-noite). Uma carga iniciada na janela termina mesmo depois dela |
| AGENDA_FUSO | fuso dos horários (padrão `America/Sao_Paulo`) |
| AGENDA_INTERVALO_S | de quanto em quanto tempo a agenda confere se há carga a iniciar (padrão 60 s) |
| AGENDA_PULAR_SEM_MUDANCA | `true` (padrão): antes de ler, compara `COUNT(*)` e `MAX(campo incremental)` de cada tabela com a última carga bem-sucedida; igual = "Sem mudança na origem", nada é lido nem reconstruído |
| AGENDA_TENTATIVAS / AGENDA_REESPERA_MIN | falha tenta de novo depois de `REESPERA_MIN` (30) até `TENTATIVAS` (3) vezes no dia |
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
