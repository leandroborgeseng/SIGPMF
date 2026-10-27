import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.rotas import api, publico
from app.core.config import VERSION, get_settings
from app.core.db import SessionLocal
from app.core.security import hash_senha
from app.explorador.rotas import explorador
from app.models.controle import Usuario

log = logging.getLogger("sigpmf")


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

    return app


app = create_app()
