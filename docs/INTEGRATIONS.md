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

## SEI: valores oficiais (não secretos)

Preencher nas variáveis do servidor (Railway/Coolify). O `.env.example` traz só os nomes.
Segredos (`SIGPMF_SRC_SEI_IDP_CLIENT_SECRET`, `SIGPMF_SRC_SEI_IDENTIFICACAO_SERVICO`) nunca entram no repositório.

| Variável | Valor (produção PRODESP/ROTA, Franca) |
|---|---|
| `SIGPMF_SRC_SEI_BASE_PROCESSOS` | `https://sei-processos.api.rota.sp.gov.br` |
| `SIGPMF_SRC_SEI_BASE_DOCUMENTOS` | `https://sei-documentos.api.rota.sp.gov.br` |
| `SIGPMF_SRC_SEI_BASE_PARAMETROS` | `https://sei-parametros.api.rota.sp.gov.br` |
| `SIGPMF_SRC_SEI_IDP_URL` | `https://rhsso.idp.sp.gov.br` |
| `SIGPMF_SRC_SEI_IDP_TOKEN_PATH` | a confirmar (vazio = conector inerte) |
| `SIGPMF_SRC_SEI_IDP_CLIENT_ID` | `prefm.centralonlinefranca` |
| `SIGPMF_SRC_SEI_SIGLA_SISTEMA` | `SEI-API-FRANCA` |
| `SIGPMF_SRC_SEI_ID_UNIDADE` | `110001339` |
| `SIGPMF_SRC_SEI_REGIONAL` | `franca` |

## SEI: pai desconhecido esgotado (`pai_desconhecido_esgotado`)

**O que o alerta significa.** Um andamento chegou com um processo pai que o SIGPMF não conhece (nem público, nem
restrito). A carga consulta esse pai no SEI no máximo `MAX_TENTATIVAS_PAI` (3) vezes; o contador fica em
`controle.sei_pais_pendentes.tentativas` e vale entre execuções. Depois da 3ª tentativa sem resposta útil, a execução
termina `parcial`, grava o log `pai_desconhecido_esgotado=N ... checkpoint_avancou=False` e devolve
`alerta="pai_desconhecido_esgotado"`. O andamento **não** é descartado em silêncio: o checkpoint do incremental fica
parado até o pai ser resolvido, então as próximas execuções reprocessam a mesma janela e continuam parciais.

**Não há rota de administração para destravar.** Não apague linhas (`DELETE`) de `sei_pais_pendentes`, da bronze nem
do checkpoint, e não avance o checkpoint à mão: isso descartaria andamentos sem trilha.

**Passo seguro do operador (manual e auditado):**

1. Identifique o pai no log da execução (`controle.logs`) e em
   `SELECT id, id_processo, ambiente_origem, tentativas, situacao, execucao_id, atualizado_em
    FROM controle.sei_pais_pendentes WHERE situacao='pendente' AND tentativas >= 3;`
2. Descubra a causa fora do SIGPMF (processo existe no SEI? a API estava fora? permissão da unidade?). Se a causa
   persiste, **não** destrave: o alerta continua sendo o comportamento correto.
3. Corrigida a causa, com aprovação registrada (chamado), rearme só aquele pai numa única transação, com auditoria:
   ```sql
   BEGIN;
   UPDATE controle.sei_pais_pendentes SET tentativas = 0, atualizado_em = now()
    WHERE id = :id AND situacao = 'pendente' AND tentativas >= 3;   -- deve afetar exatamente 1 linha
   INSERT INTO controle.auditoria (usuario, acao, alvo, detalhe)
   VALUES (:operador, 'sei_pai_rearmado', 'sei_pais_pendentes:' || :id,
           jsonb_build_object('tentativas_antes', 3, 'chamado', :chamado, 'motivo', :motivo_sem_dado_pessoal));
   COMMIT;
   ```
   Nada é apagado: a linha continua, só o contador volta a zero e a próxima execução tenta de novo (até 3 vezes).
4. Rode a próxima execução incremental na janela normal. Se o pai for resolvido, `situacao` vira `publico` ou
   `restrito` e o checkpoint avança; se esgotar de novo, o alerta volta e o caso sobe para o Tech Lead.

Não registre protocolo, nome ou outro dado do processo no `detalhe` da auditoria nem no chamado; use só o `id` interno.
