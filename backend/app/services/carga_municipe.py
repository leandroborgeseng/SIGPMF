"""Carga do Munícipe (ISS.Municipes) para a silver (aceite M1-M6).

Leitura: só pelo conector com a política ISS (lista explícita de colunas, guard SELECT-only, ROLLBACK, timeout,
TOP AMOSTRA_LIMITE). Credenciais só pelo ambiente. Os testes usam linhas sintéticas (`linhas=`).
"""
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.sqlserver.politicas import ISS
from app.core.audit import audita
from app.ingestion import municipe as mun
from app.models.camadas import Municipe, MunicipePendencia, MunicipeVersao
from app.models.controle import Execucao, Log
from app.services.diagnostico import MSG_ORIGEM_VAZIA, ORIGEM_VAZIA, FalhaLeitura, linha_json

TABELA = "Municipes"


def ler_origem(connector) -> list[dict]:
    """Amostra controlada (AMOSTRA_LIMITE, padrão 1000) de ISS.dbo.Municipes."""
    return connector.ler_amostra(TABELA, politica=ISS)


def _data(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _datahora(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def _bronze_iss(db: Session, linhas: list[dict], execucao_id: int) -> None:
    """Linhas brutas do ISS na bronze (indicador por camada). Sem PK na origem: id = hash estável da linha."""
    from app.bronze.ingestao import ingere
    regs = [{**r, "_chave_sigpmf": mun.chave_linha(mun.canonico(r))} for r in linhas]
    ingere(db, "ISS", TABELA, "_chave_sigpmf", "Data_Alteracao", regs, execucao_id, confirmar=False)


def executar_carga_municipe(db: Session, usuario: str, linhas=None, connector=None, bronze: bool = False) -> Execucao:
    ex = Execucao(tipo="carga_municipe_amostra", sistema="ISS", status="em_andamento", usuario=usuario)
    db.add(ex)
    db.commit()
    try:
        if linhas is None:
            try:
                linhas = [linha_json(r, ISS.colunas_select(TABELA)) for r in ler_origem(connector)]
            except Exception as exc:  # noqa: BLE001
                raise FalhaLeitura(TABELA, exc) from exc
        if not linhas:  # 0 linhas lidas: nunca "sem_novidades"
            ex.status, ex.mensagem = ORIGEM_VAZIA, MSG_ORIGEM_VAZIA
            ex.finalizado_em = datetime.now(UTC)
            db.add(Log(execucao_id=ex.id, nivel="WARN", mensagem=MSG_ORIGEM_VAZIA))
            db.commit()
            return ex
        if bronze:
            _bronze_iss(db, linhas, ex.id)
        existentes = dict(db.execute(select(Municipe.documento, Municipe.hash)).all())
        r = mun.classificar(linhas, existentes)
        if not r.fecha():  # M6: as somas têm de fechar com as lidas
            raise RuntimeError("Contagens do Munícipe não fecham com as linhas lidas.")
        for reg in r.registros:
            d = reg.dados
            db.merge(Municipe(documento=reg.documento, tipo=reg.tipo, tipo_pessoa=d["tipo_pessoa"], nome=d["nome"],
                              nome_mae=d["nome_mae"], data_nascimento=_data(d["data_nascimento"]),
                              pis_pasep=d["pis_pasep"], data_alteracao=_datahora(d["data_alteracao"]),
                              cidadao_id=reg.cidadao_id, confianca=reg.confianca, hash=reg.hash, execucao_id=ex.id))
        for v in r.versoes:
            ja = db.scalar(select(MunicipeVersao.id).where(MunicipeVersao.documento == v["documento"],
                                                           MunicipeVersao.hash == v["hash"]))
            if ja is None:
                db.add(MunicipeVersao(documento=v["documento"], hash=v["hash"], dados=v["dados"], execucao_id=ex.id))
        for p in r.pendencias:
            db.merge(MunicipePendencia(chave=p["chave"], motivo=p["motivo"], tipo_pessoa=p["tipo_pessoa"],
                                       documento_mascarado=p.get("documento_mascarado"),
                                       nome=p["nome"], status="pendente", execucao_id=ex.id))
        c = r.contagem
        ex.lidos, ex.novos, ex.atualizados = c["lidas"], c["novas"], c["atualizadas"]
        ex.ignorados, ex.duplicados, ex.pendentes = c["inalteradas"], c["duplicadas"], c["pendentes"]
        ex.lotes = 1
        ex.status = "sucesso" if (c["novas"] or c["atualizadas"]) else "sem_novidades"
        # Só contagens na mensagem/log: nenhum documento em claro (M7).
        ex.mensagem = (f"lidas={c['lidas']} novas={c['novas']} atualizadas={c['atualizadas']} "
                       f"inalteradas={c['inalteradas']} duplicadas={c['duplicadas']} pendentes={c['pendentes']} "
                       f"cidadaos={c['cidadaos']} empresas={c['empresas']} versoes={len(r.versoes)} "
                       f"divergencia_documento={c['divergencia_documento']}")
        db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=ex.mensagem))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        ex = db.get(Execucao, ex.id)
        ex.status = "falha"
        ex.registra_erro(exc)
        ex.mensagem = f"Falha: {type(exc).__name__}"
    ex.finalizado_em = datetime.now(UTC)
    audita(db, usuario, "carga_executada", f"execucao:{ex.id}",
           {"status": ex.status, "sistema": "ISS", "tabela": TABELA, "lidos": ex.lidos, "novos": ex.novos,
            "atualizados": ex.atualizados, "duplicados": ex.duplicados, "pendentes": ex.pendentes}, commit=False)
    db.commit()
    return ex
