"""Dicionário da natureza da dívida ativa: importação por CSV e conferência. Sem pessoa.

A dívida traz só o código (Divida.Cod_Nat). O dicionário fica em Geral.dbo.NATUREZA, base que o SIGPMF não lê (decisão de
25/10/2026): o DBA exporta o arquivo e o admin envia na tela Conectores, com as colunas COD_NATU e DESCRICAO.
Cada arquivo substitui o dicionário inteiro; arquivo inválido não grava nada. O nome chega aos cubos no próximo recálculo.
"""
import csv
import io
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.controle import Execucao

TIPO = "carga_natureza_divida"
COLUNAS = ("COD_NATU", "DESCRICAO")
MINIMO = 5


class ArquivoInvalido(ValueError):
    """Arquivo fora do formato. Mensagem segura para a tela (cita a linha, nunca o conteúdo)."""


def le_csv(conteudo: bytes) -> list[dict]:
    try:
        texto = conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = conteudo.decode("iso-8859-1")
    primeira = texto.split("\n", 1)[0]
    separador = next((s for s in (";", "\t", ",") if s in primeira), ";")  # planilha e SQL Server exportam de jeitos diferentes
    leitor = csv.reader(io.StringIO(texto), delimiter=separador)
    if tuple(c.strip().upper() for c in next(leitor, [])) != COLUNAS:
        raise ArquivoInvalido("Cabeçalho esperado: COD_NATU;DESCRICAO, sem outras colunas.")
    linhas: dict[int, str] = {}
    for n, v in enumerate(leitor, start=2):
        if not v or all(not x.strip() for x in v):
            continue
        if len(v) != 2:
            raise ArquivoInvalido(f"Linha {n}: {len(v)} colunas (esperado 2). Se a descrição tem separador, use aspas.")
        cod, desc = v[0].strip(), " ".join(v[1].split())[:200]
        if not cod.isdigit():
            raise ArquivoInvalido(f"Linha {n}: COD_NATU precisa ser número.")
        if not desc or desc.upper() == "NULL":
            continue  # código sem descrição na origem continua aparecendo como "Natureza N"
        if int(cod) in linhas:
            raise ArquivoInvalido(f"Linha {n}: o código {cod} já apareceu.")
        linhas[int(cod)] = desc
    if not linhas:
        raise ArquivoInvalido("O arquivo não tem nenhuma natureza com descrição.")
    return [{"codigo": c, "descricao": d} for c, d in linhas.items()]


def _fim(db: Session, ex: Execucao, status: str, mensagem: str, n: int) -> Execucao:
    ex.status, ex.mensagem, ex.lidos, ex.novos = status, mensagem, n, n if status == "sucesso" else 0
    ex.finalizado_em = ex.atualizado_em = datetime.now(UTC)
    db.commit()
    return ex


def importa(db: Session, usuario: str, conteudo: bytes) -> Execucao:
    """Valida o arquivo inteiro e troca o dicionário numa transação só."""
    ex = Execucao(tipo=TIPO, sistema="NATUREZA", status="em_andamento", usuario=usuario, modo="completa", atualizado_em=datetime.now(UTC))
    db.add(ex)
    db.commit()
    try:
        linhas = le_csv(conteudo)
    except ArquivoInvalido as exc:
        return _fim(db, ex, "falha", str(exc), 0)
    db.execute(text("DELETE FROM ref.divida_natureza"))
    db.execute(text("INSERT INTO ref.divida_natureza (codigo, descricao) VALUES (:codigo, :descricao)"), linhas)
    return _fim(db, ex, "sucesso", f"{len(linhas)} naturezas carregadas. Recalcule os cubos para ver os nomes nos painéis.", len(linhas))


def estado(db: Session) -> dict:
    """Quantas naturezas há e, para conferir o de-para, as mais usadas na dívida e os códigos em uso sem nome."""
    total = db.scalar(text("SELECT count(*) FROM ref.divida_natureza")) or 0
    usos = db.execute(text(f"""
        SELECT d.natureza, n.descricao, count(*) AS inscricoes FROM silver.dividas d LEFT JOIN ref.divida_natureza n ON n.codigo = d.natureza
        WHERE d.natureza IS NOT NULL GROUP BY 1, 2 HAVING count(*) >= {MINIMO} ORDER BY 3 DESC""")).all()
    ult = db.scalar(select(Execucao).where(Execucao.tipo == TIPO).order_by(Execucao.id.desc()).limit(1))
    return {"total": total,
            "mais_usadas": [{"codigo": c, "descricao": d, "inscricoes": n} for c, d, n in usos if d][:10],
            "sem_nome": [{"codigo": c, "inscricoes": n} for c, d, n in usos if not d][:20],
            "ultima_execucao": None if ult is None else {"status": ult.status, "mensagem": ult.mensagem, "finalizado_em": ult.finalizado_em}}
