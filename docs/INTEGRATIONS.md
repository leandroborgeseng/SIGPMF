# Integrações

## Conector SQL Server (somente leitura)
`backend/app/connectors/sqlserver/connector.py` — configuração só por ambiente (`SQLSERVER_*`), driver `pymssql`
(`read_only=True`), `SELECT TOP (AMOSTRA_LIMITE)` com identificadores validados, e todo SQL passa por
`core/sql_guard.py` (recusa INSERT, UPDATE, DELETE, MERGE, TRUNCATE, EXEC, DDL, GRANT, SELECT INTO, `;`).
O teste `tests/test_conector_somente_leitura.py` varre o pacote e falha se surgir comando de escrita.
Erro de conexão: mensagem sem senha, usuário, host nem string de conexão.

Nesta entrega **não há conexão com origem real**. As origens usadas são sintéticas
(`ingestion/origem_mock.py` + `ingestion/fixture_sintetica.py`): DEMO_SAUDE.prontuario e DEMO_CEMITERIO.sepultamento.

## Como adicionar um conector
1. Receber o Markdown de mapeamento (fica fora do repo, em `mapeamentos/`; pode conter dado real).
2. Cadastrar sistema e tabelas no catálogo (tela Conectores ou `POST /api/catalogo/sistemas`).
3. Acrescentar a entrada em `ingestion/mapeamentos.py` (colunas de origem → campos canônicos; só colunas permitidas).
4. Criar fixture sintética e testes (contagens esperadas, colunas proibidas fora do SELECT).
5. Validar com amostra (`AMOSTRA_LIMITE`) antes de qualquer carga completa (só admin, auditada).

Próximos: Saúde (critérios S1–S8) e Cemitério (CM1–CM11).
