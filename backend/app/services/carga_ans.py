"""Cobertura de planos de saúde: carga dos dados abertos da ANS e da população do IBGE. Sem pessoa.

Guarda, por mês: o detalhe de Franca (faixa, operadora, contrato) e o total de cada município de SP, para comparar.
Só baixa o que falta e confere de novo os dois últimos meses, que a ANS pode republicar. Falha em um mês não perde os outros.
"""
import logging
import tempfile
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.connectors.ans import arquivos as ans
from app.models.controle import Execucao

log = logging.getLogger("sigpmf.ans")
TIPO = "carga_ans"
SISTEMA = "ANS"
USUARIO_AGENDA = "agenda"
ORFA_MIN = 60
FRANCA = "351620"
RECONFERE = 2


class CargaRecusada(RuntimeError):
    """Já existe carga em andamento."""


def _fim(db: Session, ex: Execucao, status: str, mensagem: str) -> Execucao:
    ex.status, ex.mensagem, ex.etapa, ex.tabela_atual = status, mensagem, "concluida", None
    ex.finalizado_em = ex.atualizado_em = datetime.now(UTC)
    db.commit()
    return ex


def em_andamento(db: Session) -> Execucao | None:
    ex = db.scalar(select(Execucao).where(Execucao.tipo == TIPO, Execucao.status == "em_andamento").order_by(Execucao.id.desc()).limit(1))
    if ex is not None and (ex.atualizado_em or ex.iniciado_em) < datetime.now(UTC) - timedelta(minutes=ORFA_MIN):
        _fim(db, ex, "interrompida", "Carga interrompida (o servidor parou). Carregue de novo: o que já entrou fica.")
        return None
    return ex


def _nova(db: Session, usuario: str | None) -> Execucao:
    if em_andamento(db) is not None:
        raise CargaRecusada("Já existe uma carga da ANS em andamento.")
    ex = Execucao(tipo=TIPO, sistema=SISTEMA, status="em_andamento", usuario=usuario, modo="completa", etapa="bronze", atualizado_em=datetime.now(UTC))
    db.add(ex)
    db.commit()
    return ex


def _int(v: str) -> int:
    return int(v) if v.isdigit() else 0


def agrega(linhas) -> tuple[list[dict], list[dict]]:
    """Linhas da ANS -> detalhe de Franca e total por município (só beneficiário ativo conta na cobertura)."""
    detalhe: dict[tuple, list[int]] = {}
    medica: Counter = Counter()
    odonto: Counter = Counter()
    nomes: dict[str, str] = {}
    for r in linhas:
        cod = r["CD_MUNICIPIO"].strip()
        if not cod.isdigit():
            continue
        cob = "medica" if r["COBERTURA_ASSIST_PLAN"].strip().lower().startswith("m") else "odonto"
        ativos = _int(r["QT_BENEFICIARIO_ATIVO"])
        (medica if cob == "medica" else odonto)[cod] += ativos
        nomes.setdefault(cod, r["NM_MUNICIPIO"].strip()[:60])
        if cod == FRANCA:
            faixa = ans.faixa_padrao(r["DE_FAIXA_ETARIA"].strip()) or "Inconsistente"
            chave = (faixa, r["NM_RAZAO_SOCIAL"].strip()[:140] or "Não informada", r["DE_CONTRATACAO_PLANO"].strip()[:70] or "Não informado", cob)
            d = detalhe.setdefault(chave, [0, 0, 0])
            d[0] += ativos
            d[1] += _int(r["QT_BENEFICIARIO_ADERIDO"])
            d[2] += _int(r["QT_BENEFICIARIO_CANCELADO"])
    det = [{"faixa": k[0], "operadora": k[1], "contratacao": k[2], "cobertura": k[3], "ativos": v[0], "aderidos": v[1], "cancelados": v[2]} for k, v in detalhe.items()]
    mun = [{"cd_municipio": int(c), "nome": nomes[c], "medica": medica[c], "odonto": odonto[c]} for c in nomes]
    return det, mun


def grava_mes(db: Session, ano: int, mes: int, det: list[dict], mun: list[dict], execucao_id: int | None) -> None:
    """Troca o mês inteiro numa transação."""
    p = {"a": ano, "m": mes}
    db.execute(text("DELETE FROM silver.ans_beneficiarios WHERE ano = :a AND mes = :m"), p)
    db.execute(text("DELETE FROM silver.ans_municipio WHERE ano = :a AND mes = :m"), p)
    if det:
        db.execute(text("""INSERT INTO silver.ans_beneficiarios (ano, mes, faixa, operadora, contratacao, cobertura, ativos, aderidos, cancelados)
                           VALUES (:a, :m, :faixa, :operadora, :contratacao, :cobertura, :ativos, :aderidos, :cancelados)"""), [{**p, **x} for x in det])
    if mun:
        db.execute(text("INSERT INTO silver.ans_municipio (ano, mes, cd_municipio, nome, medica, odonto) VALUES (:a, :m, :cd_municipio, :nome, :medica, :odonto)"), [{**p, **x} for x in mun])
    db.execute(text("""INSERT INTO controle.ans_meses (ano, mes, execucao_id) VALUES (:a, :m, :e)
                       ON CONFLICT (ano, mes) DO UPDATE SET execucao_id = EXCLUDED.execucao_id, carregado_em = now()"""), {**p, "e": execucao_id})
    db.commit()


def carrega_populacao(db: Session, cliente: httpx.Client, anos: list[int]) -> None:
    pops = ans.populacao_estimada(cliente, anos)
    faixas = ans.censo_por_faixa(cliente)
    if pops:
        db.execute(text("""INSERT INTO ref.ibge_populacao (cd_municipio, ano, populacao) VALUES (:cd_municipio, :ano, :populacao)
                           ON CONFLICT (cd_municipio, ano) DO UPDATE SET populacao = EXCLUDED.populacao"""), pops)
    if faixas:
        db.execute(text("""INSERT INTO ref.ibge_faixa (cd_municipio, faixa, populacao) VALUES (:cd_municipio, :faixa, :populacao)
                           ON CONFLICT (cd_municipio, faixa) DO UPDATE SET populacao = EXCLUDED.populacao"""), faixas)
    db.commit()


def executar(db: Session, usuario: str | None = USUARIO_AGENDA, ex: Execucao | None = None, cliente: httpx.Client | None = None) -> Execucao:
    ex = ex or _nova(db, usuario)
    proprio = cliente is None
    cliente = cliente or httpx.Client(timeout=httpx.Timeout(60, read=300), follow_redirects=True)
    falhas: list[str] = []
    feitos = 0
    try:
        publicados = ans.lista_meses(cliente)
        carregados = {(a, m) for a, m in db.execute(text("SELECT ano, mes FROM controle.ans_meses"))}
        faltam = [x for x in publicados if x not in carregados or x in publicados[-RECONFERE:]]
        for ano, mes in faltam:
            ex.tabela_atual, ex.atualizado_em = f"{ano}-{mes:02d}", datetime.now(UTC)
            db.commit()
            try:
                with tempfile.TemporaryDirectory(prefix="sigpmf-ans-") as d:
                    zipado = Path(d) / "mes.zip"
                    ans.baixa_mes(cliente, ano, mes, zipado)
                    det, mun = agrega(ans.linhas_do_zip(zipado))
                grava_mes(db, ano, mes, det, mun, ex.id)
                feitos += 1
            except (httpx.HTTPError, ans.ArquivoAnsInvalido, OSError) as exc:
                db.rollback()
                log.warning("ANS %s-%02d não carregado: %s", ano, mes, type(exc).__name__)
                falhas.append(f"{ano}-{mes:02d}")
        ex.tabela_atual = "população (IBGE)"
        db.commit()
        anos = sorted({a for a, _m in publicados} | {datetime.now(UTC).year})
        try:
            carrega_populacao(db, cliente, anos)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, StopIteration) as exc:
            db.rollback()
            log.warning("IBGE não carregado: %s", type(exc).__name__)
            falhas.append("população do IBGE")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return _fim(db, ex, "falha", f"Carga da ANS não concluída ({type(exc).__name__}).")
    finally:
        if proprio:
            cliente.close()
    ex.lidos = feitos
    if falhas:
        return _fim(db, ex, "parcial", f"{feitos} meses carregados. Faltaram: {', '.join(falhas[:6])}{'...' if len(falhas) > 6 else ''}.")
    return _fim(db, ex, "sucesso", f"{feitos} meses carregados. Recalcule os cubos para atualizar a estimativa por bairro.")


def iniciar_em_fundo(db: Session, usuario: str) -> Execucao:
    ex = _nova(db, usuario)
    disparar(ex.id)
    return ex


def disparar(ex_id: int) -> None:
    """Background (thread daemon). Os testes trocam por execução síncrona."""
    threading.Thread(target=rodar, args=(ex_id,), daemon=True, name=f"ans-{ex_id}").start()


def rodar(ex_id: int) -> None:
    from app.core.db import SessionLocal
    db = SessionLocal()
    try:
        ex = db.get(Execucao, ex_id)
        if ex is not None and ex.status == "em_andamento":
            executar(db, ex.usuario, ex=ex)
    except Exception:  # noqa: BLE001
        log.exception("ANS: execução %s caiu fora do laço", ex_id)
    finally:
        db.close()


def estado(db: Session) -> dict:
    def d(e: Execucao | None) -> dict | None:
        return None if e is None else {"status": e.status, "mensagem": e.mensagem, "passo": e.tabela_atual, "lidos": e.lidos, "finalizado_em": e.finalizado_em}
    meses = db.execute(text("SELECT count(*), min(ano * 100 + mes), max(ano * 100 + mes) FROM controle.ans_meses")).one()
    ult = db.scalar(select(Execucao).where(Execucao.tipo == TIPO).order_by(Execucao.id.desc()).limit(1))
    return {"meses": meses[0], "de": meses[1], "ate": meses[2], "em_andamento": d(em_andamento(db)), "ultima_execucao": d(ult)}
