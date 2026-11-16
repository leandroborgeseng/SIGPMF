"""POST /api/chat. Autenticado como as demais rotas (usuario_atual). Só leitura."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.chat.catalogo import ForaDoCatalogo
from app.chat.litellm import Indisponivel
from app.chat.servico import responder
from app.core.db import get_db
from app.core.security import usuario_atual
from app.models.controle import Usuario

chat = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(usuario_atual)])

MSG_LLM = "O serviço de linguagem não está disponível agora. Tente de novo em instantes."
MSG_FORA = ("Essa pergunta está fora das consultas disponíveis. Dá para perguntar a última carga de uma fonte, "
            "as empresas da última carga (SIL ou ISS) ou os registros de uma fonte.")


class PerguntaIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    pergunta: str


def _ip(req: Request) -> str | None:
    return req.client.host if req.client else None


@chat.post("")
def perguntar(dados: PerguntaIn, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    texto = (dados.pergunta or "").strip()
    if not texto or len(texto) > 500:
        raise HTTPException(422, "Escreva uma pergunta de até 500 caracteres.")
    try:
        return responder(db, u, texto, _ip(req))
    except Indisponivel:
        raise HTTPException(503, MSG_LLM) from None
    except ForaDoCatalogo:
        raise HTTPException(422, MSG_FORA) from None
