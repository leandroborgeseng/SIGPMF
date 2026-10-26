# Modelo de dados (migrações `backend/alembic/versions`)

| Schema | Tabela | Conteúdo |
|---|---|---|
| controle | sistemas, tabelas_catalogo | catálogo (sistema, tabela, chave, campo incremental) |
| controle | execucoes, logs | cada carga: status (sucesso/sem_novidades/falha), lidos, novos, atualizados, ignorados, lotes, início/fim; log por lote |
| controle | usuarios | perfis admin, admin_lgpd, operador, leitura (senha bcrypt) |
| controle | auditoria | quem, quando (timestamptz), ação, alvo, detalhe JSONB — append-only |
| controle | parametros | limiar e pesos da correlação |
| bronze | registros | sistema_origem, tabela, id_origem, dados JSONB, hash SHA-256, execucao_id, atualizado_origem_em, extraido_em; único (origem, tabela, id, hash) |
| silver | cidadaos, identificadores | cadastro central; identificador CPF (normalizado) |
| silver | sistemas, vinculos | vínculo registro de origem ↔ cidadão com método, confiança e atributos |
| silver | enderecos | todos os endereços com fonte e data; `mais_recente` marcado |
| silver | correlacoes_pendentes, inconsistencias | revisão (ambiguo, revisao) e problemas (cpf_invalido, cpf_nascimento_divergente) |
| gold | cidadao_sistemas, cidadao_dados | por cidadão: sistemas/domínios; valor mais recente por campo com fonte e data (sem CPF) |

Silver e gold são reconstruídas de forma determinística a partir do bronze (idempotente).
Retenção configurável: fora do MVP.
