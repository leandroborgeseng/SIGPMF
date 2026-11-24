"""Rua e número do imóvel a partir de bronze.sil_imoveis_fisico.

O número só existe em imovelendereco (`CEP - TIPO LOGRADOURO, NNNNN  complemento`).
A inscrição é só dígitos; 12 ou 13 dígitos completam com zeros até 14, e o inverso também
casa (a mesma regra da inscrição imobiliária). Uma consulta para o lote inteiro.
Não lê CPF/CNPJ nem nome do responsável.
"""
import re
from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.identity.inscricao_imobiliaria import TAMANHO

_CEP_VIA_NUM = re.compile(r"^\s*\d{5}-\d{3}\s*-\s*(?P<via>.+?)\s*,\s*(?P<num>\d{5})(?:\s.*)?\s*$")
_MIUDAS = frozenset({"de", "da", "do", "das", "dos", "e"})
_LIMITE = 300
_CURTAS = (12, 13)

# Só endereço do imóvel. Sem cpfcnpjresponsavel, nomeresponsavel ou endereço de entrega.
_COLUNAS = """
SELECT DISTINCT ON (digitos)
       bronze.inscricao_so_digitos(inscrfisico) AS digitos,
       imoveltplogradouro, imovellogradouro, imovelendereco, imovelbairro
FROM bronze.sil_imoveis_fisico
"""
_ORDEM = "ORDER BY digitos, atualizado_em DESC NULLS LAST, idfisico DESC"

SQL_ENDERECOS = _COLUNAS + """
WHERE bronze.inscricao_so_digitos(inscrfisico) IN (
    SELECT bronze.inscricao_so_digitos(codi)
    FROM silver.imovel_possuidores
    WHERE cidadao_id IS NOT NULL AND desde IS NOT NULL
      AND bronze.inscricao_so_digitos(codi) <> ''
)
""" + _ORDEM

SQL_ENDERECOS_LOTE = _COLUNAS + """
WHERE bronze.inscricao_so_digitos(inscrfisico) = ANY(:digitos)
""" + _ORDEM


def so_digitos(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def chaves_inscricao(valor: str | None) -> list[str]:
    """Dígitos da inscrição e a forma com zeros à esquerda até 14, nos dois sentidos."""
    d = so_digitos(valor)
    if not d:
        return []
    chaves = [d]
    if len(d) in _CURTAS:
        chaves.append(d.zfill(TAMANHO))
    elif len(d) == TAMANHO:
        curto = d.lstrip("0")
        if len(curto) in _CURTAS:
            chaves.append(curto)
    return chaves


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


def _guardar(out: dict[str, str], digitos: str | None, rotulo: str | None) -> None:
    if not rotulo or not digitos:
        return
    out[digitos] = rotulo
    if len(digitos) in _CURTAS:
        out[digitos.zfill(TAMANHO)] = rotulo


def _mapa_de_linhas(linhas) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in linhas:
        _guardar(out, r.digitos, rotulo_endereco(r.imoveltplogradouro, r.imovellogradouro, r.imovelendereco, r.imovelbairro))
    return out


def _chaves_busca(inscricoes: Iterable[str | None]) -> list[str]:
    visto: list[str] = []
    marca: set[str] = set()
    for valor in inscricoes:
        for chave in chaves_inscricao(valor):
            if chave not in marca:
                marca.add(chave)
                visto.append(chave)
    return visto


def enderecos_por_inscricoes(db: Session, inscricoes: Iterable[str | None]) -> dict[str, str]:
    """Inscrição (formatada, com ou sem zeros) → rótulo. Uma consulta para o lote; vazio não consulta."""
    digitos = _chaves_busca(inscricoes)
    if not digitos:
        return {}
    return _mapa_de_linhas(db.execute(text(SQL_ENDERECOS_LOTE), {"digitos": digitos}))


def rotulo_da_inscricao(mapa: dict[str, str], valor: str | None) -> str | None:
    for chave in chaves_inscricao(valor):
        rotulo = mapa.get(chave)
        if rotulo:
            return rotulo
    return None


def endereco_do_imovel(mapa: dict[str, str], codi: str | None, inscricao: str | None) -> str | None:
    """Mesma chave da linha do tempo (CODI) e, se diferir, a inscrição exibida."""
    return rotulo_da_inscricao(mapa, codi) or rotulo_da_inscricao(mapa, inscricao)


def mapa_enderecos(db: Session) -> dict[str, str]:
    """Inscrição só com dígitos → rótulo. Uma ida ao banco para todos os eventos de posse."""
    return _mapa_de_linhas(db.execute(text(SQL_ENDERECOS)))
