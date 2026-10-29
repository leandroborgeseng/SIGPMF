from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bronze.ingestao import ingere
from app.connectors.sqlserver.politicas import TabelaRecusada, politica
from app.core.audit import audita
from app.core.config import get_settings
from app.gold import visao
from app.ingestion.mapeamentos import MAPEAMENTOS, NOMES_SISTEMAS
from app.ingestion.origem_mock import OrigemMock
from app.models.controle import Execucao, Log, Sistema, TabelaCatalogo
from app.silver import processamento

# Carga sintética (demo/OrigemMock) só com APP_ENV exatamente "dev" ou "test" (alinhado ao #7).
# "development", vazio, ausente (padrão do código = "prod"), prod etc. -> bloqueada.
AMBIENTES_SINTETICOS = frozenset({"dev", "test"})


class CargaSinteticaBloqueada(PermissionError):
    pass


def carga_sintetica_permitida() -> bool:
    return get_settings().app_env in AMBIENTES_SINTETICOS


def garante_catalogo_demo(db: Session, sistemas: set[str] | None = None) -> None:
    for (codigo, tabela), m in MAPEAMENTOS.items():
        if sistemas is not None and codigo not in sistemas:
            continue
        sis = db.scalar(select(Sistema).where(Sistema.codigo == codigo))
        if sis is None:
            sis = Sistema(codigo=codigo, nome=NOMES_SISTEMAS.get(codigo, codigo), dominio=m["dominio"],
                          descricao="Sistema sintético da demonstração")
            db.add(sis)
            db.flush()
        if db.scalar(select(TabelaCatalogo).where(TabelaCatalogo.sistema_id == sis.id, TabelaCatalogo.nome == tabela)) is None:
            ch = m["chave"]
            db.add(TabelaCatalogo(sistema_id=sis.id, nome=tabela, chave=",".join(ch) if isinstance(ch, tuple) else ch,
                                  campo_incremental=m["campo_incremental"]))
    db.commit()


def executar_demo(db: Session, usuario: str, completa: bool = False, origem: OrigemMock | None = None,
                  antes_do_lote=None, conjunto: str = "base") -> Execucao:
    """Carga de demonstração. Padrão: amostra (AMOSTRA_LIMITE, 100 por tabela). `completa` só por admin, auditada.
    Sistemas com política (SAUDE, CEMITERIO): tabela fora da lista é recusada e auditada; só colunas permitidas."""
    s = get_settings()
    if not carga_sintetica_permitida():  # vale também para o CLI (app/demo.py)
        audita(db, usuario, "carga_demo_negada", "conector:demo", {"ambiente": s.app_env})
        raise CargaSinteticaBloqueada("Carga sintética bloqueada fora de APP_ENV=dev/test")
    origem = origem or OrigemMock(conjunto=conjunto)
    garante_catalogo_demo(db, {sis for sis, _ in origem.tabelas()})
    limite = None if completa else s.amostra_limite
    nome_conj = "DEMO" if conjunto == "base" else conjunto.upper()
    ex = Execucao(tipo="carga_demo_completa" if completa else "carga_demo_amostra", sistema=nome_conj,
                  status="em_andamento", usuario=usuario)
    db.add(ex)
    db.commit()
    tot = {"lidos": 0, "novos": 0, "atualizados": 0, "ignorados": 0, "lotes": 0}
    try:
        for sistema, tabela in origem.tabelas():
            pol = politica(sistema)
            colunas = None
            if pol is not None:
                try:
                    colunas = pol.colunas_select(tabela)
                except TabelaRecusada as exc:
                    db.add(Log(execucao_id=ex.id, nivel="WARN", mensagem=str(exc)))
                    audita(db, usuario, "tabela_recusada", f"{sistema}.{tabela}", {"codigo": exc.codigo, "motivo": str(exc)},
                           commit=False)
                    db.commit()
                    continue
            m = MAPEAMENTOS.get((sistema, tabela))
            if m is None:  # permitida, mas ainda sem mapeamento para a silver: não carrega nesta entrega
                continue
            c = ingere(db, sistema, tabela, m["chave"], m["campo_incremental"], origem.ler(sistema, tabela, limite, colunas),
                       ex.id, s.lote_tamanho, antes_do_lote, rowversion=m.get("rowversion"))
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
           {"status": ex.status, "completa": completa, "conjunto": conjunto, **tot}, commit=False)
    db.commit()
    return ex
