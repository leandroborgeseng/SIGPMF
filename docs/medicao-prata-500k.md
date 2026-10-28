# Medição bronze → prata (500 mil linhas sintéticas ZZTESTE)

Ambiente: box de testes (8 vCPU, 15 GB), Postgres 17 local, banco `sigpmf_prata_perf_test`, `python -m tests.bench_prata 500000`.
Bronze sintética de `tests/bench_ouro.py` (~70% com CPF válido, 30% sem CPF, endereço em todas). Reconstrução sobre uma
prata já populada (caso real do dia a dia). Nenhum dado real.

| etapa | s | % | RSS pico (MB) |
|---|---:|---:|---:|
| limpa_prata (DELETE das 7 tabelas) | 11,9 | 10,6 | 68 |
| le_bronze_canonico | 8,1 | 7,2 | 706 |
| indice_cns | 0,1 | 0,1 | 714 |
| passe_cpf (normaliza + DV + uuid + objetos ORM) | 19,3 | 17,1 | 1.949 |
| passe_cns | 0,3 | 0,2 | 1.958 |
| passe_atributos (índice nome/nasc/mãe) | 12,7 | 11,3 | 2.315 |
| grava_cidadaos | 6,8 | 6,0 | 2.321 |
| grava_identificadores | 7,9 | 7,0 | 2.321 |
| grava_pendentes_inconsistencias | 3,3 | 2,9 | 2.323 |
| grava_vinculos | 28,5 | 25,3 | 2.868 |
| grava_enderecos | 11,4 | 10,1 | 2.898 |
| commit | 2,3 | 2,1 | 2.898 |
| **total** | **112,6** | 100 | **2.898** |

Gravação (DELETE + INSERTs + commit): ~72 s (64%). Não há consulta por linha (1.218 idas ao banco no total) e nem regex
quente; o custo é construir 1,8 milhão de objetos ORM e mandá-los por executemany (um bind por linha).
