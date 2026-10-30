import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.acesso_ti import ti
from app.api.rotas import api, publico
from app.connectors.sei.rotas import sei
from app.contagens.rotas import contagens
from app.core.config import VERSION, get_settings
from app.core.db import SessionLocal
from app.core.security import hash_senha
from app.explorador.rotas import explorador
from app.models.controle import Usuario

log = logging.getLogger("sigpmf")


def configurar_logs() -> logging.Logger:
    """A aplicação não configura o logging raiz e o uvicorn só configura os loggers dele: sem isto o INFO da medição da
    prata (app.silver.medicao, só etapa/números, sem PII) era descartado. Handler próprio em stdout, sem propagar para o
    raiz (não duplica se alguém configurar o raiz). Idempotente."""
    import sys
    med = logging.getLogger("app.silver.medicao")
    med.setLevel(logging.INFO)
    med.propagate = False
    if not any(getattr(h, "_sigpmf_medicao", False) for h in med.handlers):
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        h._sigpmf_medicao = True
        med.addHandler(h)
    med.disabled = False
    # SIL (TL 30/10): sem handler, o INFO de sigpmf.conector.sil / sigpmf.sil caía no lastResort do logging (só
    # WARNING+), e a 349 falhou sem nenhuma linha. Os loggers do SIL só registram método, caminho sem query, status,
    # tentativa e Sequencia; httpx/httpcore seguem em WARNING (logariam a URL com a query).
    for nome in ("sigpmf.conector.sil", "sigpmf.sil"):
        lg = logging.getLogger(nome)
        lg.setLevel(logging.INFO)
        lg.propagate = False
        if not any(getattr(h, "_sigpmf_stdout", False) for h in lg.handlers):
            h = logging.StreamHandler(sys.stdout)
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            h._sigpmf_stdout = True
            lg.addHandler(h)
        lg.disabled = False
    for nome in ("httpx", "httpcore"):
        logging.getLogger(nome).setLevel(logging.WARNING)
    return med


configurar_logs()


def bootstrap_admin() -> None:
    """Cria o primeiro admin a partir de ADMIN_EMAIL/ADMIN_PASSWORD se não houver usuários."""
    s = get_settings()
    if not (s.admin_email and s.admin_password):
        return
    db = SessionLocal()
    try:
        if db.scalar(select(Usuario).limit(1)) is None:
            db.add(Usuario(email=s.admin_email.lower(), nome="Administrador", perfil="admin",
                           senha_hash=hash_senha(s.admin_password)))
            db.commit()
            log.info("Usuário admin inicial criado")
    finally:
        db.close()


def interromper_cargas_orfas() -> int:
    """Startup: carga completa em_andamento sem processo vivo (trava livre) vira 'interrompida' (CC7)."""
    from app.services.carga_completa import interromper_orfas
    db = SessionLocal()
    try:
        return interromper_orfas(db)
    finally:
        db.close()


def iniciar_agenda():
    """AG1/AG5: agenda das cargas só com AGENDA_HABILITADA=true e nunca em APP_ENV=test (os testes chamam o ciclo)."""
    s = get_settings()
    if not s.agenda_habilitada or s.app_env == "test":
        return None
    from app.services.agenda import Agendador
    a = Agendador()
    a.iniciar()
    return a


def create_app() -> FastAPI:
    s = get_settings()
    if s.app_env != "test" and s.segredo_chave_hash_ok() is None:  # PR-03: sem o segredo do HMAC não sobe
        raise RuntimeError("SIGPMF_CHAVE_HASH_SEGREDO obrigatório fora de testes (mínimo 32 caracteres)")
    docs = s.app_env == "dev" and s.docs_habilitado
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            bootstrap_admin()
        except Exception as exc:  # noqa: BLE001
            log.warning("bootstrap_admin falhou: %s", type(exc).__name__)
        try:
            interromper_cargas_orfas()
        except Exception as exc:  # noqa: BLE001
            log.warning("interromper_cargas_orfas falhou: %s", type(exc).__name__)
        agendador = iniciar_agenda()
        try:  # releitura de uso único (SIGPMF_RELEITURA_FONTE): thread que só começa após o /health; não bloqueia
            from app.services.releitura_deploy import iniciar_em_fundo
            iniciar_em_fundo()
        except Exception as exc:  # noqa: BLE001
            log.warning("releitura_deploy não iniciou: %s", type(exc).__name__)
        if s.app_env != "test":  # aquece o retrato das contagens de /api/conectores em fundo (não bloqueia o boot)
            try:
                from app.services.contagens_cache import aquecer_em_fundo
                aquecer_em_fundo()
            except Exception as exc:  # noqa: BLE001
                log.warning("aquecer contagens falhou: %s", type(exc).__name__)
        yield
        if agendador is not None:
            agendador.parar()

    app = FastAPI(title="SIGPMF", lifespan=lifespan, version=VERSION, docs_url="/api/docs" if docs else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if docs else None)
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins.split(","), allow_methods=["*"],
                           allow_headers=["*"])
    app.include_router(publico)
    app.include_router(publico, prefix="/api")
    app.include_router(api, prefix="/api")
    app.include_router(explorador, prefix="/api")  # EX1–EX7
    app.include_router(sei, prefix="/api")  # SEI1–SEI8
    app.include_router(ti, prefix="/api")  # PR-06 R5/R6
    app.include_router(contagens, prefix="/api")  # PR-07 J1–J4

    return app


app = create_app()
