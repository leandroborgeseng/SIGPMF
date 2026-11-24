# HE — de-para de lotação por segmento (FL5/FL8/FL9)

Escopo: **só modelagem**. Sem carga real, sem ingestão Senior (DBSENIOR segue bloqueado; PR #81 não mergeado), sem deploy.

## O que entra
- **Migração 0045** (`backend/alembic/versions/0045_lotacao_secretaria.py`), aditiva e idempotente: cria `ref.lotacao_secretaria`
  (`codloc_secretaria`, `nome`, `grupo` ∈ {`secretaria`,`outros_vinculos`,`sem_lotacao`}, `ordem`) com a ESTRUTURA do organograma 7
  (17 códigos, sem pessoa). Downgrade não apaga nada (convenção da 0044).
- **`backend/app/paineis/lotacao.py`**: `Resolvedor.resolve(codloc)` → secretaria (`1.x`) e órgão (`1.x.y`) por segmentos;
  `descendente()` (igual ou `sec + '.'`); `sql_descendente()` → `(codloc = :sec OR codloc LIKE :sec || '.%')`;
  `agrupa_drill()` → ranking (12 secretarias, k aplicado via `privacidade.suprime`), blocos **"Outros vínculos"** e
  **"Sem lotação"** (k por grupo), total geral e média da prefeitura (só ranking).

## Regra
| codloc | resultado |
|---|---|
| `1.10.3` | Saúde (`1.10`), órgão `1.10.3` — **não** Gabinete |
| `1.1.5` | Gabinete do Prefeito, órgão `1.1.5` |
| `1.12`–`1.15` (e descendentes) | Outros vínculos — fora do ranking e da média, dentro do total |
| `1`, vazio, desconhecido | Sem lotação — dentro do total |

## Ligação com o painel HE
Não existe ainda dataset HE com `codloc` (o painel `folha-modelo` da #82 usa nomes sintéticos em `nivel1`). O resolvedor e o
agrupamento ficam prontos para a onda 1: a carga da folha deve gravar em `gold.painel_fato.nivel1` o código da secretaria
resolvido (ou o grupo) e o drill usa `agrupa_drill`/`sql_descendente`. Nada de `LIKE sec || '%'` nem `LEFT(codloc, n)`.

## Evidência
- `ruff check .` — ver `ruff.txt`
- pytest completo em Postgres local `sigpmf_he_lotacao_test` — ver `pytest.txt`
- Testes novos: `backend/tests/test_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_lotacao_secretaria.py` (fixture ZZTESTE sintética)
