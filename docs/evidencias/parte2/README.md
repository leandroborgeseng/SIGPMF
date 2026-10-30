# Parte 2: prata incremental (evidência, ZZTESTE, banco local *_test)

Bancada: `backend/scripts/evidencia_parte2.py` (cada fase num subprocesso; tempo de parede e RSS máx. via os.wait4).
JSONs: `bancada_iptu_cadastro_6_1M.json` (100k pessoas + 6,1M IPTU.Cadastro + 500 chaves de homologação SIL) e
`bancada_pequena_200k.json` (20k + 200k). A recomposição da 6,1M rodou em paralelo com o pytest (tempo inflado).

| Volume | ultima_versao (1º preenchimento) | Recomposição | Incremental (1% do Cadastro) | PI5 |
|---|---|---|---|---|
| 6,1M | 80,6 s / 77 MB | 1843,9 s / 534 MB (2ª: 3020,2 s / 529 MB, com contenção) | 221,1 s e 391,0 s / 108 MB | idêntico |
| 200k | 2,3 s / 77 MB | 48,6 s / 237 MB | 3,4 s e 3,2 s / 105 MB | idêntico |

O incremental é dominado pelo ouro recomposto inteiro (D7; ouro incremental = Parte 3).

D6: 500 chaves só de homologação na bronze, 0 na prata (diferença esperada contra o predicado antigo, que só
filtrava quarentena; o teste `test_pi5_diferenca_esperada_so_nas_chaves_de_homologacao` confere a igualdade exata).

EXPLAIN (ANALYZE, BUFFERS), no banco _test: ver a chave `explain` dos JSONs (`completo_antes`/`completo_depois`,
`incremental_antes`/`incremental_depois`). Na 6,1M o completo ficou em ~0,9 s antes e ~1,0 s depois (Parallel Seq Scan
nos dois: as pessoas são 1,6% da bronze). Na 200k, o delta de uma rodada (2.000 chaves) levou 1,1 ms antes e 14 ms
depois (Bitmap em ix_ultima_versao_registro). O ganho da ultima_versao está na correção, não no tempo: versão em
quarentena ou de homologação não gera delta nem substitui a vigente. A produção fica com a DevOps.

D4: o `hash` atual é sha256 do JSON de `dados` com chaves ordenadas (`bronze.ingestao.hash_registro`). Só não é
conteúdo puro em dois casos: com `rowversion` configurado (vira sha256("rowversion:"+rv); hoje nenhuma tabela usa) e em
homologação (prefixo do ambiente). `hash_conteudo` (nulo, sem backfill) segue o ajuste do TL das 08h57.
