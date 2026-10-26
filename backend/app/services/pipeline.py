from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bronze.ingestao import ingere
from app.core.audit import audita
from app.core.config import get_settings
from app.gold import visao
from app.ingestion.mapeamentos import MAPEAMENTOS, NOMES_SISTEMAS
from app.ingestion.origem_mock import OrigemMock
from app.models.controle import Execucao, Sistema, TabelaCatalogo
from app.silver import processamento


def garante_catalogo_demo(db: Session) -> None:
    for (codigo, tabela), m in MAPEAMENTOS.items():
        sis = db.scalar(select(Sistema).where(Sistema.codigo == codigo))
        if sis is None:
            sis = Sistema(codigo=codigo, nome=NOMES_SISTEMAS.get(codigo, codigo), dominio=m["dominio"],
                          descricao="Sistema sintético da demonstração")
            db.add(sis)
            db.flush()
        if db.scalar(select(TabelaCatalogo).where(TabelaCatalogo.sistema_id == sis.id, TabelaCatalogo.nome == tabela)) is None:
            db.add(TabelaCatalogo(sistema_id=sis.id, nome=tabela, chave=m["chave"], campo_incremental=m["campo_incremental"]))
    db.commit()


def executar_demo(db: Session, usuario: str, completa: bool = False, origem: OrigemMock | None = None,
                  antes_do_lote=None) -> Execucao:
    """Carga de demonstração. Padrão: amostra (AMOSTRA_LIMITE, 100). `completa` só por admin, auditada."""
    s = get_settings()
    garante_catalogo_demo(db)
    origem = origem or OrigemMock()
    limite = None if completa else s.amostra_limite
    ex = Execucao(tipo="carga_demo_completa" if completa else "carga_demo_amostra", sistema="DEMO",
                  status="em_andamento", usuario=usuario)
    db.add(ex)
    db.commit()
    tot = {"lidos": 0, "novos": 0, "atualizados": 0, "ignorados": 0, "lotes": 0}
    try:
        for sistema, tabela in origem.tabelas():
            m = MAPEAMENTOS[(sistema, tabela)]
            c = ingere(db, sistema, tabela, m["chave"], m["campo_incremental"], origem.ler(sistema, tabela, limite),
                       ex.id, s.lote_tamanho, antes_do_lote)
            for k in tot:
                tot[k] += c[k]
        sres = processamento.reconstruir(db)
        gres = visao.reconstruir(db)
        ex.status = "sucesso" if (tot["novos"] or tot["atualizados"]) else "sem_novidades"
        ex.mensagem = f"silver={sres} gold={gres}"
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        ex = db.get(Execucao, ex.id)
        ex.status = "falha"
        ex.mensagem = f"Falha: {type(exc).__name__}"
    for k, v in tot.items():
        setattr(ex, k, v)
    ex.finalizado_em = datetime.now(UTC)
    audita(db, usuario, "carga_executada", f"execucao:{ex.id}",
           {"status": ex.status, "completa": completa, **tot}, commit=False)
    db.commit()
    return ex
