from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models.controle import Usuario

PERFIS = ("admin", "admin_lgpd", "operador", "leitura")
_bearer = HTTPBearer(auto_error=False)


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()


def confere_senha(senha: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode(), hashed.encode())
    except ValueError:
        return False


def cria_token(usuario: Usuario) -> str:
    s = get_settings()
    agora = datetime.now(UTC)
    payload = {"sub": usuario.email, "perfil": usuario.perfil, "iat": agora,
               "exp": agora + timedelta(minutes=s.jwt_expira_minutos)}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def usuario_atual(cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
                  db: Session = Depends(get_db)) -> Usuario:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Não autenticado")
    try:
        payload = jwt.decode(cred.credentials, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido ou expirado") from None
    u = db.scalar(select(Usuario).where(Usuario.email == payload.get("sub"), Usuario.ativo.is_(True)))
    if u is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário inválido")
    return u


def exige_perfil(*perfis: str):
    def dep(u: Usuario = Depends(usuario_atual)) -> Usuario:
        if u.perfil not in perfis:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Perfil sem permissão para esta ação")
        return u
    return dep


CARGA = exige_perfil("admin", "operador")
REVELA_CPF = exige_perfil("admin_lgpd")
ADMIN = exige_perfil("admin")
