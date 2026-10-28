# Modelo de dados (migrações `backend/alembic/versions`)

| Schema | Tabela | Conteúdo |
|---|---|---|
| controle | sistemas, tabelas_catalogo | catálogo (sistema, tabela, chave, campo incremental) |
| controle | execucoes, logs | cada carga: status (sucesso/sem_novidades/falha), lidos, novos, atualizados, ignorados, lotes, início/fim; log por lote |
| controle | usuarios | perfis admin, admin_lgpd, operador, leitura (senha bcrypt) |
| controle | auditoria | quem, quando (timestamptz), ação, alvo, detalhe JSONB — append-only |
| controle | parametros | limiar e pesos da correlação |
| controle | camadas_estado | assinatura das entradas da última reconstrução bem-sucedida de prata e ouro (0012) |
| controle | agendamentos | agenda das cargas completas (0011): conector, horário, ativo, ordem, forçar, último status/mensagem/execução, tentativas, próxima tentativa, assinatura da origem (COUNT e MAX do campo incremental por tabela); `ultima_completa_em` (0012) |
| bronze | registros | sistema_origem, tabela, id_origem, dados JSONB, hash SHA-256, execucao_id, atualizado_origem_em, extraido_em; único (origem, tabela, id, hash) |
| silver | cidadaos, identificadores | cadastro central; identificador CPF (normalizado) |
| silver | sistemas, vinculos | vínculo registro de origem ↔ cidadão com método, confiança e atributos |
| silver | enderecos | todos os endereços com fonte e data; `mais_recente` marcado |
| silver | correlacoes_pendentes, inconsistencias | revisão (ambiguo, revisao) e problemas (cpf_invalido, cpf_nascimento_divergente) |
| silver | imoveis, imovel_possuidores, lancamentos, dividas | prata tributária (0013): fatos de IPTU/Dívida/Financeiro ligados ao cidadão pelo CPF já correlacionado; nunca criam cidadão |
| silver | unidades_saude, atendimentos, agendamentos, vacinacoes | fatos da Saúde sem conteúdo clínico (0014), cidadão pela matrícula |
| silver | eventos | linha do tempo do cidadão, todas as áreas (0014) |
| gold | indicadores | ouro agregado dos painéis: série, período, dimensão, valor; sem pessoa (0014) |
| gold | cidadao_sistemas, cidadao_dados | por cidadão: sistemas/domínios; valor mais recente por campo com fonte e data (sem CPF) |

Silver e gold são reconstruídas de forma determinística a partir do bronze (idempotente).
Retenção configurável: fora do MVP.
