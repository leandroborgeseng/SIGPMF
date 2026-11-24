# SLA S01 — shell de painéis (local)

Medido em 04/11/2026 22:02 (horário de Brasília), uvicorn local (1 worker) + Postgres local, banco `sigpmf_shell_demo_test` com o painel de exemplo (864 linhas sintéticas). Script: `backend/scripts/sla_paineis.py`.

API: http://127.0.0.1:18700 · aquecimento 5 · medição 20 chamadas por endpoint · limite p95 < 2 s

| Endpoint | n | mín (ms) | mediana (ms) | p95 (ms) | máx (ms) | OK |
|---|---|---|---|---|---|---|
| folha-modelo (prefeitura) | 20 | 4.7 | 6.0 | 6.9 | 7.2 | sim |
| folha-modelo competência 2026-07 | 20 | 5.2 | 5.9 | 8.0 | 8.9 | sim |
| folha-modelo › Saúde | 20 | 4.8 | 5.2 | 6.4 | 7.2 | sim |
| folha-modelo › Saúde › órgão | 20 | 4.5 | 5.0 | 6.3 | 6.3 | sim |

Observação: medição local, não na rede interna do SRV01; a gold de produção ainda não tem fato da folha (só exemplo).
