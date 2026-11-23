"""Conferência cadastral + posse do contato existente. Nenhum acesso por respostas pessoais isoladas."""
import hashlib
import hmac
import secrets
import smtplib
import ssl
import unicodedata
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from fastapi import HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.config import get_settings
from app.identity.cpf import normaliza
from app.models.camadas import Cidadao, DadoGold, Identificador
from app.portal_cidadao.modelos import AcessoCidadao

COOKIE = "franca_cidadao"
GENERICA = "Se os dados conferirem e houver e-mail cadastrado, você receberá um código. Caso não receba, solicite atualização cadastral no atendimento municipal."


def agora():
    return datetime.now(UTC)


def utc(d):
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d


def resumo_hash(tipo, valor):
    return hmac.new(get_settings().jwt_secret.encode(), f"portal:{tipo}:{valor}".encode(), hashlib.sha256).hexdigest()


def nome_normalizado(v):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", v).casefold() if not unicodedata.combining(c)).split())


def limitar(db, req: Request, cpf=""):
    # Limites persistidos e compartilhados entre processos. Não confia em X-Forwarded-For enviado pelo cliente.
    a = agora()
    db.execute(delete(AcessoCidadao).where(AcessoCidadao.expira_em < a - timedelta(days=1)).execution_options(synchronize_session=False))
    for tipo, valor, teto in (("ip", req.client.host if req.client else "desconhecido", 30), ("cpf", cpf, 6)):
        if not valor:
            continue
        chave = resumo_hash(tipo, valor)
        inserir = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
        db.execute(inserir(AcessoCidadao).values(chave=chave, tipo="limite", tentativas=0,
                     expira_em=a + timedelta(minutes=15)).on_conflict_do_nothing(index_elements=["chave"]))
        r = db.scalar(select(AcessoCidadao).where(AcessoCidadao.chave == chave).with_for_update())
        if utc(r.expira_em) <= a:
            r.tentativas = 0
            r.expira_em = a + timedelta(minutes=15)
        if r.tentativas >= teto:
            db.commit()
            raise HTTPException(429, "Muitas tentativas. Aguarde 15 minutos para tentar novamente.")
        r.tentativas += 1
    db.commit()


def conferir(db, cpf, nascimento, mae):
    cid = db.scalar(select(Identificador.cidadao_id).where(Identificador.tipo == "cpf", Identificador.valor == normaliza(cpf)))
    c = db.get(Cidadao, cid) if cid else None
    if not c or c.falecido or c.data_nascimento != nascimento:
        return None, None
    dados = list(db.scalars(select(DadoGold).where(DadoGold.cidadao_id == cid, DadoGold.mais_recente.is_(True),
                                                 DadoGold.campo.in_(["nome_mae", "email"])) ))
    maes = {nome_normalizado(d.valor or "") for d in dados if d.campo == "nome_mae"}
    emails = {(d.valor or "").strip().lower() for d in dados if d.campo == "email"}
    # Divergência cadastral não escolhe uma identidade/contato arbitrariamente.
    if len(maes) != 1 or nome_normalizado(mae) not in maes or len(emails) != 1:
        return None, None
    email = next(iter(emails))
    if "@" not in email or "\n" in email or "\r" in email:
        return None, None
    return cid, email


def enviar_codigo(email, codigo):
    s = get_settings()
    msg = EmailMessage()
    msg["Subject"] = "Seu código de acesso · Prefeitura de Franca"
    msg["From"] = s.portal_smtp_from
    msg["To"] = email
    msg.set_content(f"Seu código de acesso ao Portal do Cidadão é {codigo}.\nVálido por 10 minutos. Não compartilhe.\nSe você não solicitou, desconsidere.")
    # Sem log de destinatário, conteúdo ou credenciais. Falha de entrega não revela existência de cadastro.
    try:
        with smtplib.SMTP_SSL(s.portal_smtp_host, s.portal_smtp_port, timeout=15, context=ssl.create_default_context()) as smtp:
            if s.portal_smtp_user and s.portal_smtp_password:
                smtp.login(s.portal_smtp_user, s.portal_smtp_password.get_secret_value())
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException):
        return False
    return True


def sessao(db, req):
    token = req.cookies.get(COOKIE, "")
    r = db.get(AcessoCidadao, resumo_hash("sessao", token)) if token else None
    if not r or r.tipo != "sessao" or utc(r.expira_em) <= agora() or not r.cidadao_id:
        raise HTTPException(401, "Entre novamente para consultar seus dados.")
    return r


def criar_sessao(db, cid):
    token = secrets.token_urlsafe(32)
    db.add(AcessoCidadao(chave=resumo_hash("sessao", token), tipo="sessao", cidadao_id=cid,
                         tentativas=0, expira_em=agora() + timedelta(minutes=15)))
    return token
