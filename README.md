# SIGPMF — Integração de dados municipais (Prefeitura de Franca)

Bronze → Silver → Gold em PostgreSQL, com cadastro central do cidadão, conectores **somente leitura** para os
SQL Server municipais e painel administrativo. **Entrega 1 (MVP):** esqueleto, login com perfis e auditoria,
catálogo, conector SQL Server (estrutura), demo sintética ponta a ponta e 3 telas (Dashboard, Conectores, Simulador).

> Só dados **sintéticos** no repositório. Mapeamentos reais (`mapeamentos/`), dumps e `.env` nunca são versionados.

## Subir com Docker (recomendado)
```bash
cp .env.example .env          # troque TODOS os CHANGE_ME (senhas, JWT_SECRET >= 32 caracteres)
docker compose up -d --build  # postgres, backend (roda alembic upgrade head) e frontend (nginx)
docker compose ps             # os 3 serviços ficam "healthy"
```
Abra `http://localhost:${HTTP_PORT}` (padrão 8080) e entre com `ADMIN_EMAIL`/`ADMIN_PASSWORD` (o primeiro admin é
criado na primeira subida). O Postgres **não** publica porta no host; só o frontend publica `HTTP_PORT`, e o
nginx encaminha `/api` ao backend.

### Demo sintética
Pela tela **Conectores → Executar carga (demo)** (perfil operador ou admin) ou:
```bash
docker compose exec backend python -m app.demo            # amostra (AMOSTRA_LIMITE, padrão 100 por tabela)
docker compose exec backend python -m app.demo --completa # carga completa (só via CLI ou admin; auditada)
```
Rodar duas vezes dá `sem_novidades` e as mesmas contagens (A14). Contagens esperadas:
`backend/tests/fixtures/expected_counts.json`.

### Usuários e perfis
| Perfil | Pode |
|---|---|
| `admin` | catálogo, usuários, configurações, executar cargas; vê CPF mascarado |
| `admin_lgpd` | único que revela CPF (motivo obrigatório, ≥ `REVELAR_MOTIVO_MIN`, auditado) |
| `operador` | executa conectores/cargas; catálogo → 403; CPF mascarado |
| `leitura` | só consulta; CPF mascarado |

Criar usuário (admin): `POST /api/usuarios {"email","nome","perfil","senha"}`.

## Desenvolvimento local (sem Docker)
```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://USUARIO@localhost:PORTA/sigpmf JWT_SECRET=...  # Postgres local
alembic upgrade head && uvicorn app.main:app --reload
TEST_DATABASE_URL=postgresql+psycopg://USUARIO@localhost:PORTA/sigpmf_test pytest   # banco de TESTE (é recriado)
ruff check .
cd ../frontend && npm ci && npm run dev    # proxy /api -> localhost:8000 (VITE_API_PROXY)
npm test && npm run lint && npm run build
```

## Documentação
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — camadas, módulos, segurança
- [docs/DATA_MODEL.md](docs/DATA_MODEL.md) — schemas e tabelas
- [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) — conectores, como adicionar um novo
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — variáveis, backup/restore, operação
