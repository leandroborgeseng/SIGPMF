# Plano: trava de amostra com heartbeat + contador da prata (factory/sigpmf-carga-trava)

## Causa da trava órfã
- `backend/app/services/carga_conector.py:110-117`: dupla trava. (1) `pg_try_advisory_lock(crc32("amostra:<SISTEMA>"))` em conexão dedicada (morre com o processo); (2) `controle.execucoes` com `tipo IN ('amostra_conector','carga_municipe_amostra') AND sistema=... AND status='em_andamento'` -> 409 "Já existe uma amostra de ... em andamento".
- Restart no meio da amostra: o advisory lock some, mas a linha fica `em_andamento` para sempre. Não expira por tempo.
- `app/main.py:34-56` + `carga_completa.py:227` (`interromper_orfas`) só limpam `tipo='carga_completa_conector'`; amostra não é limpa no boot.
- A amostra não grava `atualizado_em` (só a carga completa usa `sinal_de_vida`).

## Backend (sem migration: `atualizado_em` já existe desde 0009)
1. Heartbeat: amostra grava `atualizado_em` (conexão separada, como `sinal_de_vida`) no início, a cada tabela lida e a cada lote ingerido, e antes da prata/ouro.
2. Boot: `interromper_orfas` passa a cobrir `TIPOS_AMOSTRA` (trava advisory livre -> `status='interrompida'`, log WARN, `finalizado_em`).
3. No 409: se a linha `em_andamento` existe mas o advisory lock está livre (processo morto), marcar `interrompida` e seguir (auto-cura, auditado).
4. `GET /conectores/{c}/amostra/bloqueio` (operador): `{tipo, iniciado_em, atualizado_em, parada_min, pode_encerrar}`; sem PII.
5. `POST /conectores/{c}/amostra/{id}/encerrar` (só admin, motivo obrigatório, audita): só se `now - coalesce(atualizado_em, iniciado_em) >= 10 min` (`SIGPMF_AMOSTRA_PARADA_MIN`, padrão 10) e advisory lock livre; vira `status='falha'`, mensagem "Encerrada por <admin>: parada sem atividade". Nunca apaga nada.

## Frontend (proposta do Designer)
- Aviso âmbar: "Amostra de Cemitério em andamento · iniciada às HH:MM · última atividade há N min".
- Botão "Carregar amostra" desabilitado com o motivo visível (texto abaixo, não só tooltip).
- Após 10 min sem atividade e só para admin: "Encerrar amostra parada" com diálogo de confirmação (motivo).

## Contador da prata
- Causa: `backend/app/silver/processamento.py:186-189` (`passo`) é chamado em 214 (pessoa+terceiro), 268 (sem_ident, subconjunto já contado em 214) e 301 (óbitos); total = `len(regs)` (184). Registros sem CPF/CNS contam 2x -> passa do total. Revisão/cns_extra não contam. Duplicação real: não (vinculos UNIQUE (sistema,tabela,chave_origem), reconstrução apaga e regrava).
- Correção: contar 1x por registro da bronze tratado (set de `r.id` ou contar só na decisão final), `min(feitos, total)`; ouro/vínculos numa linha separada "Registros gerados na prata: N" (vinculos + pendentes) no card, nunca na barra.
- Testes: 6000 ZZTESTE sem CPF -> progresso nunca > total; barra = 100% no fim.
