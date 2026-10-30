"""Parte 2 (0038): manutenção de bronze.ultima_versao — a versão mais recente SEM quarentena de cada chave.

- `atualiza` (na carga, `bronze.ingestao.ingere`): INSERT ... ON CONFLICT DO UPDATE só se o id novo for maior.
  Só chega aqui linha sem quarentena (a quarentena tem caminho próprio e não atualiza a tabela).
- D6: predicado único `quarentena IS NULL AND ambiente_origem = 'producao'` (o mesmo do índice e da prata).
- `sincroniza` (preenchimento inicial e acerto): em lotes de faixa de id, idempotente, com o mesmo filtro
  quarentena IS NULL. Fora do boot: scripts/preenche_ultima_versao.py e o início de cada prata/ouro (só o resto).
Só chaves e ids: nenhum dado pessoal.
"""
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.camadas import RegistroBronze, UltimaVersao
from app.models.controle import CamadasMarca

LOTE_IDS = 200_000
ANALISA_ACIMA = 50_000  # ids sincronizados de uma vez acima disso -> ANALYZE (o autovacuum pode estar atrás)
MARCA = ("_ultima_versao", "_")  # linha especial em controle.camadas_marca: até que id da bronze já foi sincronizado

_UPSERT_FAIXA = text("""
INSERT INTO bronze.ultima_versao (sistema_origem, tabela, id_origem, registro_id)
SELECT DISTINCT ON (sistema_origem, tabela, id_origem) sistema_origem, tabela, id_origem, id
  FROM bronze.registros
 WHERE quarentena IS NULL AND ambiente_origem = 'producao' AND id > :de AND id <= :ate
 ORDER BY sistema_origem, tabela, id_origem, id DESC
ON CONFLICT (sistema_origem, tabela, id_origem)
DO UPDATE SET registro_id = EXCLUDED.registro_id WHERE EXCLUDED.registro_id > bronze.ultima_versao.registro_id
""")


def atualiza(db: Session, sistema: str, tabela: str, linhas, ambiente: str = "producao") -> None:
    """linhas: [(id_origem, id)] recém-inseridas, sem quarentena. Linha de homologação/sandbox não entra (D6)."""
    if ambiente != "producao":
        return
    vals = {}
    for io, rid in linhas:
        if rid is not None and (io not in vals or rid > vals[io]):
            vals[io] = rid
    if not vals:
        return
    st = insert(UltimaVersao).values([{"sistema_origem": sistema, "tabela": tabela, "id_origem": io, "registro_id": r}
                                      for io, r in vals.items()])
    db.execute(st.on_conflict_do_update(
        index_elements=["sistema_origem", "tabela", "id_origem"], set_={"registro_id": st.excluded.registro_id},
        where=st.excluded.registro_id > UltimaVersao.registro_id))


def sincroniza(db: Session, ate: int | None = None, lote: int = LOTE_IDS, pulso=None, confirmar: bool = True) -> int:
    """Leva ultima_versao até o id `ate` da bronze (padrão: o maior), a partir da última faixa sincronizada.
    Cada faixa é um commit (se `confirmar`): interrompida, retoma da última faixa confirmada. Devolve o id final."""
    m = db.get(CamadasMarca, MARCA)
    if m is None:
        m = CamadasMarca(sistema=MARCA[0], tabela=MARCA[1], ultimo_id=0, expandido_ate=0)
        db.add(m)
        db.flush()
    alvo = ate if ate is not None else int(db.scalar(select(func.max(RegistroBronze.id))) or 0)
    de = inicio = int(m.ultimo_id or 0)
    while de < alvo:
        ate_f = min(de + lote, alvo)
        db.execute(_UPSERT_FAIXA, {"de": de, "ate": ate_f})
        m.ultimo_id = de = ate_f
        if confirmar:
            db.commit()
        else:
            db.flush()
        if pulso is not None:
            pulso.grava()
    if alvo - inicio > ANALISA_ACIMA:  # tabela cresceu muito (1º preenchimento): estatística nova antes do plano
        db.execute(text("ANALYZE bronze.ultima_versao"))
        if confirmar:
            db.commit()
    return de
