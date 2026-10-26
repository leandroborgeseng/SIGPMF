# Arquitetura (entrega 1)

```
SQL Server municipais ──(somente SELECT, amostra)──► bronze (JSONB bruto + hash + execução)
                                                        │ versão mais recente por id de origem
                                                        ▼
                                                     silver (cidadãos, identificadores, vínculos,
                                                             endereços, pendências, inconsistências)
                                                        ▼
                                                     gold (visão por cidadão e domínio, com fonte e data)
```
- **backend/** FastAPI + SQLAlchemy 2 + Alembic. Pastas: `api` (rotas), `core` (config por env, db, segurança JWT,
  auditoria, guarda de SQL), `models`, `connectors/sqlserver`, `ingestion` (mapeamentos, origem mock, fixture),
  `bronze`, `silver`, `identity` (CPF, correlação), `gold`, `services` (pipeline).
- **frontend/** React + TypeScript + Vite servido por nginx (proxy `/api`).
- **docker-compose.yml** postgres (rede interna, volume `pgdata`), backend, frontend; todos com healthcheck e restart.

## Correlação (A13 v2/v3)
1. CPF válido idêntico → score 1,0, associa (`cidadao_id` = uuid5 do CPF).
2. Mesmo CPF com nascimento divergente → `ambiguo`, pendente.
3. Sem CPF válido → score por nome normalizado, nascimento e mãe (pesos em `controle.parametros`).
   ≥ 0,85 → `revisao` (não associa nesta entrega). < 0,85 → novo `cidadao_id`.
4. Divergências: a gold mostra o valor mais recente (`fonte_atualizado_em`); as demais versões ficam na silver/bronze.

## Segurança
- Senhas bcrypt; JWT HS256 (`JWT_SECRET`), expiração configurável.
- Toda rota `/api/*` exige token, exceto `/api/health` e `/api/auth/login`.
- `controle.auditoria` é append-only: trigger bloqueia UPDATE/DELETE/TRUNCATE; não há rota de alteração.
- CPF mascarado (`***.456.789-**`) em API, CSV, mensagens e logs; revelação só `admin_lgpd` com motivo.
- `/api/docs` e OpenAPI só com `APP_ENV=dev` **e** `DOCS_HABILITADO=true`.
- Conector: só `SELECT`/`WITH` (guarda em `core/sql_guard.py` + teste por varredura do pacote).
