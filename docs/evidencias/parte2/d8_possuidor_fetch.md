# D8: incremental do Possuidor parado em FETCH FORWARD (diagnóstico e correção)

Banco próprio `sigpmf_d8_test` (ZZTESTE: 200k Cadastro + 200k Cadastro_Possuidor), EXPLAIN sem ANALYZE.

**Causa.** `tributario._ultimas(codis=...)` (recálculo por CODI, chamado por `recalcula_codis` para posses e
imóvel) fazia, num cursor no servidor (yield_per = FETCH FORWARD), um
`id IN (SELECT max(id) ... WHERE left(btrim(dados->>'CODI'),22) IN (<2000 CODIs>) GROUP BY id_origem) ORDER BY id`.
O índice `ix_registros_iptu_codi` existe, mas a estimativa da expressão do índice parcial não entra no plano: o filtro
foi estimado em ~todas as linhas da tabela e o plano virou Seq Scan + HashAggregate da tabela inteira a cada lote
de 2000 CODIs. O primeiro FETCH só volta depois de agregar tudo; com 6,1M + 6,1M linhas, >20 min por lote.

Antes (DECLARE ... CURSOR, igual ao plano sem cursor):
```
Merge Join (r.id = ANY_subquery.max)
  -> Index Scan using registros_pkey on registros r            (rows=400000)
  -> Sort -> HashAggregate -> Subquery Scan -> HashAggregate (Group Key: id_origem)
        -> Seq Scan on registros                               (rows=198658)
```

**Correção** (`tributario._ultimas`, chaves/CODIs): lotes de `LOTE_CHAVES` (500), um `LATERAL` por valor com
`OFFSET 0` (o planejador não achata e sonda o índice por valor), sem cursor no servidor; a última versão por
id_origem (max id, mesmo predicado D6) é escolhida no Python e devolvida na ordem de id (desempate igual).
`pulso()` a cada lote. Na carga, o `subetapa.Pulso` (thread) já grava o sinal a cada 30 s; na bancada não havia.

Depois:
```
Nested Loop
  -> Function Scan on unnest k                                  (rows=500)
  -> Bitmap Heap Scan on registros -> Bitmap Index Scan on ix_registros_iptu_codi   (CODI = k.v)
-- por chave (Cadastro, Financeiro, Divida, fatos da Saúde):
Nested Loop -> Index Only Scan using ix_bronze_registros_ultima (sistema_origem, tabela, id_origem = k.v)
```
Medido no banco de 200k: 2000 CODIs de posses em 0,07 s; 2000 chaves do Cadastro em 0,05 s.
Teste: `test_d8_recalculo_por_codi_em_lotes_com_pulso_e_mesma_versao` (mesmo resultado do max(id), pulso por lote).
