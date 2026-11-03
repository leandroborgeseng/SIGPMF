"""Carga do AUDESP (AU1–AU3): portal do TCE-SP (ou upload) -> bronze -> prata, um arquivo por (tipo, ano).

- Por agenda ou "Executar agora": para cada ano configurado e cada tipo (receitas, despesas, restos a pagar), HEAD no
  portal; assinatura igual à do último arquivo carregado -> pula (a execução diária termina em segundos). Mudou -> baixa,
  confere o SHA-256 (conteúdo igual -> só atualiza a assinatura) e carrega.
- Cada arquivo é tudo ou nada: 1ª passada valida o arquivo inteiro (cabeçalho, ano, mês, valores) sem gravar; a 2ª grava
  bronze (append-only, hash por linha) e troca a prata daquele (tipo, ano) numa transação só.
- Um arquivo com problema não impede os outros: a execução termina `parcial` (algum falhou) ou `falha` (todos falharam).
- Uma carga do AUDESP por vez. Mensagens e logs só com tipo, ano e contagens; nunca conteúdo de linha.
"""
import logging
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bronze.ingestao import ingere
from app.connectors.audesp import arquivos as aq
from app.models.controle import Execucao, Log
from app.models.financas import ArquivoAudesp
from app.silver import financas

log = logging.getLogger("sigpmf.audesp")
TIPO = "carga_audesp"
USUARIO_AGENDA = "agenda"
LOTE = 2000
NOMES_TIPO = {"receitas": "receitas", "despesas": "despesas", "restos_a_pagar": "restos a pagar"}


class CargaRecusada(RuntimeError):
    """Já existe carga do AUDESP em andamento."""


def em_andamento(db: Session) -> Execucao | None:
    return db.scalar(select(Execucao).where(Execucao.tipo == TIPO, Execucao.status == "em_andamento")
                     .order_by(Execucao.id.desc()).limit(1))


def ultima(db: Session) -> Execucao | None:
    return db.scalar(select(Execucao).where(Execucao.tipo == TIPO).order_by(Execucao.id.desc()).limit(1))


def _log(db: Session, ex: Execucao, mensagem: str, nivel: str = "INFO") -> None:
    db.add(Log(execucao_id=ex.id, nivel=nivel, mensagem=mensagem[:500]))


def _nova(db: Session, usuario: str | None) -> Execucao:
    if em_andamento(db) is not None:
        raise CargaRecusada("Já existe uma carga do AUDESP em andamento.")
    ex = Execucao(tipo=TIPO, sistema=aq.SISTEMA, status="em_andamento", usuario=usuario, modo="incremental",
                  etapa="leitura", atualizado_em=datetime.now(UTC))
    db.add(ex)
    db.commit()
    return ex


def _valida(conteudo: bytes, tipo_esperado: str | None, ano_esperado: int | None) -> tuple[str, int, int, int | None]:
    """1ª passada, sem gravar: (tipo, ano do arquivo, linhas, último mês). Levanta ArquivoInvalido/LinhaInvalida."""
    tipo, gen = aq.linhas(aq.abre_csv(conteudo))
    if tipo_esperado and tipo != tipo_esperado:
        raise aq.ArquivoInvalido(f"O arquivo de {NOMES_TIPO[tipo_esperado]} veio com o cabeçalho de {NOMES_TIPO[tipo]}.")
    proj = financas.PROJECAO[tipo]
    anos: set[int] = set()
    meses: dict[int, int] = {}
    n = 0
    for r in gen:
        a = aq.ano_da_linha(tipo, r)
        p = proj(r, a or 0)  # valida valor, mês e fase da linha
        mes = p.get("mes", p.get("mes_balancete"))
        if a is not None:
            anos.add(a)
            meses[a] = max(meses.get(a, 0), mes)
        n += 1
    if n == 0:
        raise aq.ArquivoInvalido("O arquivo não tem nenhuma linha de dados.")
    if ano_esperado is not None:
        ano = ano_esperado
    elif tipo == "restos_a_pagar":
        ano = max(anos)  # o arquivo do ano traz também o encerramento (mês 13) do ano anterior
    elif len(anos) == 1:
        ano = anos.pop()
    else:
        raise aq.ArquivoInvalido("O arquivo mistura mais de um exercício.")
    if tipo != "restos_a_pagar" and anos and anos != {ano}:
        raise aq.ArquivoInvalido(f"O arquivo de {ano} traz linhas de outro exercício.")
    return tipo, ano, n, meses.get(ano)


def processa(db: Session, ex: Execucao, conteudo: bytes, origem: str, assinatura: str | None = None,
             tipo_esperado: str | None = None, ano_esperado: int | None = None) -> dict:
    """Valida e carrega um arquivo (zip do TCE ou CSV do upload). Tudo ou nada: confirma no fim ou desfaz."""
    tipo, ano, n, ultimo_mes = _valida(conteudo, tipo_esperado, ano_esperado)
    chave = aq.CHAVE[tipo]
    cont = {"lidos": 0, "novos": 0, "atualizados": 0, "ignorados": 0, "lotes": 0}

    def lotes():
        _t, gen = aq.linhas(aq.abre_csv(conteudo))
        buf = []
        for r in gen:
            buf.append(r)
            if len(buf) >= LOTE:
                yield buf
                buf = []
        if buf:
            yield buf
    try:
        prata = financas.troca_ano(db, tipo, ano, _ingere_e_repassa(db, ex, tipo, chave, lotes(), cont))
        reg = db.scalar(select(ArquivoAudesp).where(ArquivoAudesp.tipo == tipo, ArquivoAudesp.ano == ano))
        if reg is None:
            reg = ArquivoAudesp(tipo=tipo, ano=ano)
            db.add(reg)
        reg.origem, reg.assinatura, reg.sha256 = origem, assinatura, aq.sha256(conteudo)
        reg.linhas, reg.ultimo_mes, reg.execucao_id, reg.carregado_em = n, ultimo_mes, ex.id, datetime.now(UTC)
        for k in ("lidos", "novos", "atualizados", "ignorados", "lotes"):
            setattr(ex, k, (getattr(ex, k) or 0) + cont[k])
        _log(db, ex, f"AUDESP {NOMES_TIPO[tipo]} {ano}: {n} linhas, até o mês {ultimo_mes}; bronze novos={cont['novos']} "
                     f"atualizados={cont['atualizados']} iguais={cont['ignorados']}; prata={prata}.")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"tipo": tipo, "ano": ano, "linhas": n, "ultimo_mes": ultimo_mes, "prata": prata, **cont}


def _ingere_e_repassa(db: Session, ex: Execucao, tipo: str, chave: str, lotes, cont: dict):
    """Grava cada lote na bronze (sem confirmar) e repassa as linhas para a prata, na mesma transação."""
    for lote in lotes:
        c = ingere(db, aq.SISTEMA, tipo, chave, None, lote, ex.id, lote=LOTE, confirmar=False)
        for k in cont:
            cont[k] += c[k]
        yield from lote


def executar(db: Session, usuario: str | None = USUARIO_AGENDA, cfg: aq.ConfigAudesp | None = None,
             cliente: httpx.Client | None = None, forcar: bool = False,
             depois_do_arquivo: Callable[[dict], None] | None = None) -> Execucao:
    """Confere e carrega os arquivos do TCE. `forcar` ignora a assinatura (ainda pula conteúdo idêntico pelo SHA-256)."""
    cfg = cfg or aq.ConfigAudesp.do_ambiente()
    ex = _nova(db, usuario)
    proprio = cliente is None
    cliente = cliente or aq.cliente_http()
    carregados, pulados, falhas = [], 0, []
    try:
        for ano in cfg.anos:
            for tipo in aq.TIPOS:
                nome = f"{NOMES_TIPO[tipo]} {ano}"
                ex.tabela_atual, ex.atualizado_em = nome, datetime.now(UTC)
                db.commit()
                try:
                    url = cfg.url(tipo, ano)
                    sig = aq.assinatura(cliente, url)
                    if sig is None:
                        _log(db, ex, f"AUDESP {nome}: ainda não publicado pelo TCE.")
                        pulados += 1
                        continue
                    reg = db.scalar(select(ArquivoAudesp).where(ArquivoAudesp.tipo == tipo, ArquivoAudesp.ano == ano))
                    if reg is not None and not forcar and reg.assinatura == sig.texto():
                        pulados += 1
                        continue
                    conteudo = aq.baixa(cliente, url)
                    if reg is not None and reg.sha256 == aq.sha256(conteudo):
                        reg.assinatura, reg.origem = sig.texto(), "tce"
                        _log(db, ex, f"AUDESP {nome}: republicado sem mudança no conteúdo.")
                        db.commit()
                        pulados += 1
                        continue
                    r = processa(db, ex, conteudo, "tce", sig.texto(), tipo_esperado=tipo, ano_esperado=ano)
                    carregados.append(r)
                    if depois_do_arquivo:
                        depois_do_arquivo(r)
                except (aq.ArquivoInvalido, aq.ErroDownload, financas.LinhaInvalida, PermissionError) as exc:
                    db.rollback()
                    falhas.append(nome)
                    _log(db, ex, f"AUDESP {nome}: {exc}", "WARN")
                    db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return _fim(db, ex, "falha", f"Carga do AUDESP interrompida ({type(exc).__name__}).")
    finally:
        if proprio:
            cliente.close()
    return _fim(db, ex, *_resultado(carregados, pulados, falhas))


def _resultado(carregados: list[dict], pulados: int, falhas: list[str]) -> tuple[str, str]:
    feitos = "; ".join(f"{NOMES_TIPO[r['tipo']]} {r['ano']} até o mês {r['ultimo_mes']}" for r in carregados)
    if falhas and not carregados and not pulados:
        return "falha", f"Nenhum arquivo do AUDESP carregado. Com problema: {', '.join(falhas)}."
    if falhas:
        return "parcial", f"Carregados: {feitos or 'nenhum'}. Com problema: {', '.join(falhas)}."
    if not carregados:
        return "sem_novidades", "Nenhum arquivo novo no portal do TCE."
    return "sucesso", f"Carregados: {feitos}."


def _fim(db: Session, ex: Execucao, status: str, mensagem: str) -> Execucao:
    ex.status, ex.mensagem, ex.etapa = status, mensagem, "concluida"
    ex.tabela_atual, ex.finalizado_em, ex.atualizado_em = None, datetime.now(UTC), datetime.now(UTC)
    db.commit()
    log.info("AUDESP execução %s: %s", ex.id, status)
    return ex


def carrega_upload(db: Session, usuario: str, conteudo: bytes) -> Execucao:
    """Plano B: arquivo enviado pela tela (zip do TCE ou o CSV). O tipo vem do cabeçalho; o ano, das linhas."""
    ex = _nova(db, usuario)
    try:
        r = processa(db, ex, conteudo, "upload")
    except (aq.ArquivoInvalido, financas.LinhaInvalida) as exc:
        db.rollback()
        return _fim(db, ex, "falha", str(exc))
    return _fim(db, ex, "sucesso", f"Carregado: {NOMES_TIPO[r['tipo']]} {r['ano']} até o mês {r['ultimo_mes']} "
                                   f"({r['linhas']} linhas).")


def arquivos(db: Session) -> list[dict]:
    return [{"tipo": a.tipo, "ano": a.ano, "origem": a.origem, "linhas": a.linhas, "ultimo_mes": a.ultimo_mes,
             "publicado_tce": a.assinatura.split("|")[0] if a.assinatura else None, "carregado_em": a.carregado_em,
             "execucao_id": a.execucao_id}
            for a in db.scalars(select(ArquivoAudesp).order_by(ArquivoAudesp.ano.desc(), ArquivoAudesp.tipo))]
