"""Rua e número do imóvel na linha do tempo, a partir de bronze.sil_imoveis_fisico.

O número só existe em imovelendereco (`CEP - TIPO LOGRADOURO, NNNNN  complemento`).
Uma consulta para a timeline inteira. Não lê CPF/CNPJ nem nome do responsável.
"""
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

_CEP_VIA_NUM = re.compile(r"^\s*\d{5}-\d{3}\s*-\s*(?P<via>.+?)\s*,\s*(?P<num>\d{5})(?:\s.*)?\s*$")
_MIUDAS = frozenset({"de", "da", "do", "das", "dos", "e"})
_LIMITE = 300

# Só endereço do imóvel. Sem cpfcnpjresponsavel, nomeresponsavel ou endereço de entrega.
SQL_ENDERECOS = """
SELECT DISTINCT ON (digitos)
       bronze.inscricao_so_digitos(inscrfisico) AS digitos,
       imoveltplogradouro, imovellogradouro, imovelendereco, imovelbairro
FROM bronze.sil_imoveis_fisico
WHERE bronze.inscricao_so_digitos(inscrfisico) IN (
    SELECT bronze.inscricao_so_digitos(codi)
    FROM silver.imovel_possuidores
    WHERE cidadao_id IS NOT NULL AND desde IS NOT NULL
      AND bronze.inscricao_so_digitos(codi) <> ''
)
ORDER BY digitos, atualizado_em DESC NULLS LAST, idfisico DESC
"""


def so_digitos(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def _limpo(valor) -> str | None:
    if valor is None:
        return None
    texto = " ".join(str(valor).split())
    return texto or None


def _titulo(texto: str) -> str:
    saida = []
    for i, palavra in enumerate(texto.split()):
        baixa = palavra.casefold()
        if i and baixa in _MIUDAS:
            saida.append(baixa)
        else:
            saida.append(baixa[:1].upper() + baixa[1:])
    return " ".join(saida)


def _com_bairro(via: str, numero: str | None, bairro: str | None) -> str:
    miolo = f"{via}, {numero}" if numero else via
    if bairro:
        miolo = f"{miolo} - {_titulo(bairro)}"
    return miolo


def rotulo_endereco(tplogradouro, logradouro, endereco, bairro) -> str | None:
    """Via e número a partir de imovelendereco. Parse inválido: tipo + logradouro + bairro, sem número."""
    bruto = _limpo(endereco)
    achou = _CEP_VIA_NUM.match(bruto) if bruto else None
    if achou:
        via = _limpo(achou.group("via"))
        if via:
            numero = achou.group("num").lstrip("0") or None
            return _com_bairro(_titulo(via), numero, _limpo(bairro))
    via_fb = " ".join(p for p in (_limpo(tplogradouro), _limpo(logradouro)) if p)
    if not via_fb:
        return None
    return _com_bairro(_titulo(via_fb), None, _limpo(bairro))


def titulo_posse(codi: str, rotulo: str | None) -> str:
    base = f"Passou a constar como possuidor do imóvel {codi}"
    if rotulo:
        base = f"{base} ({rotulo})"
    return base[:_LIMITE]


def mapa_enderecos(db: Session) -> dict[str, str]:
    """Inscrição só com dígitos → rótulo. Uma ida ao banco para todos os eventos de posse."""
    out: dict[str, str] = {}
    for r in db.execute(text(SQL_ENDERECOS)):
        rotulo = rotulo_endereco(r.imoveltplogradouro, r.imovellogradouro, r.imovelendereco, r.imovelbairro)
        if rotulo and r.digitos:
            out[r.digitos] = rotulo
    return out
