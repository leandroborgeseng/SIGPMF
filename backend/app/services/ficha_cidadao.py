"""Ficha do cidadão: túmulos e sepultamento (cemitério) e o que cada área da ficha já tem carregado.

A ficha mostra todas as áreas, inclusive as vazias. "Não tem registro" só vale quando a base foi carregada; por isso
`fontes` diz, sem nenhum dado de pessoa, quais tabelas têm linhas e quais bases ainda não fazem parte do SIGPMF.

Cemitério na ficha: vínculos do cidadão (titular, autorizado e leilão por CPF válido; sepultamento só com óbito
confirmado por pessoa). Nome de terceiros, endereço e documento da linha de origem não saem dessa lista.

Titulares da sepultura: a versão mais recente de cada linha de `CEMITERIO.Titulares` daquela sepultura. A flag
`Ativo` (S/N) diz quem é o atual. `Inclusao` é um código, não uma data, e não sai. `CAD_CEMITE` guarda um titular
só (e documento) e `Ocorrencia_Sepultura` não tem titular: nenhum dos dois entra nesta lista.
"""
from sqlalchemy import literal, select, text
from sqlalchemy.orm import Session

from app.models.camadas import (
    Agendamento,
    Atendimento,
    DividaAtiva,
    EmpresaSil,
    EscolaAluno,
    ImovelPossuidor,
    Lancamento,
    RegistroBronze,
    Vacinacao,
    Vinculo,
)
from app.silver.tributario import _ultimas
from app.util.datas import normaliza_data_iso

_MODELOS = {"vacinas": Vacinacao, "consultas": Atendimento, "agendamentos": Agendamento, "imoveis": ImovelPossuidor,
            "lancamentos": Lancamento, "dividas": DividaAtiva, "empresas": EmpresaSil, "educacao": EscolaAluno}
# (área, base no catálogo): existem na prefeitura, mas o SIGPMF ainda não lê; a ficha não pode dizer "não tem"
NAO_INTEGRADAS = (("Assistência social", "CadastroUnico"),
                  ("Protocolo", "Protocolo_WEB"), ("Exames de laboratório", "Labor"),
                  ("Medicamentos dispensados", "IntegracaoSaudeTerceiros"), ("ITBI", "ITBI"), ("Multas de trânsito", "Transito"))


def fontes(db: Session) -> dict:
    tem = {k: db.scalar(select(literal(1)).select_from(m).limit(1)) is not None for k, m in _MODELOS.items()}
    tem["cemiterio"] = db.scalar(select(RegistroBronze.id).where(RegistroBronze.sistema_origem == "CEMITERIO",
                                                                 RegistroBronze.tabela == "Titulares").limit(1)) is not None
    return {"carregadas": tem, "nao_integradas": [a for a, _b in NAO_INTEGRADAS]}


def _cod(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _txt(v) -> str | None:
    return str(v).strip() or None if v is not None else None


def _data(v) -> str | None:
    """Só data de verdade: Titulares.Inclusao, por exemplo, guarda um código de uma letra."""
    return normaliza_data_iso(v) if v else None


def cemiterio(db: Session, cid: str) -> dict:
    nomes = {_cod(d.get("codcemite")): _txt(d.get("nomecemite")) for _io, d in _ultimas(db, "CEMITERIO", "Cemiterio")}

    def lugar(d: dict) -> dict:
        cod = d.get("Cod_Cemiterio", d.get("COD_CEMITERIO"))
        return {"cod_cemiterio": None if cod is None else (str(cod).strip() or None),
                "cemiterio": nomes.get(_cod(cod)) or (f"Cemitério {str(cod).strip()}" if cod is not None else None),
                "quadra": _txt(d.get("Quadra", d.get("QUADRA"))), "sepultura": _txt(d.get("Sepultura", d.get("SEPULTURA")))}

    out = {"titular": [], "autorizado": [], "leilao": [], "sepultamento": []}
    q = (select(Vinculo.tabela, RegistroBronze.dados).join(RegistroBronze, RegistroBronze.id == Vinculo.bronze_id)
         .where(Vinculo.cidadao_id == cid, Vinculo.sistema == "CEMITERIO").order_by(Vinculo.id))
    for tabela, d in db.execute(q):
        if tabela == "Titulares":
            ativo = str(d.get("Ativo") or "").strip().upper()
            out["titular"].append({**lugar(d), "ativo": True if ativo == "S" else False if ativo == "N" else None})
        elif tabela == "Pessoas_Autorizadas":
            out["autorizado"].append({**lugar(d), "parentesco": _txt(d.get("Parentesco"))})
        elif tabela == "Leilao":
            out["leilao"].append({**lugar(d), "data_venda": _data(d.get("DataVenda")), "data_liberacao": _data(d.get("DataLiberacao"))})
        elif "QUADRA" in d:  # Obitos: só existe vínculo com óbito confirmado
            out["sepultamento"].append({**lugar(d), "gaveta": _txt(d.get("GAVETA")), "lado": _txt(d.get("LADO")),
                                        "data_morte": _data(d.get("DT_MORTE")), "data_sepultamento": _data(d.get("DT_SEPULTADO"))})
    out["titular"].sort(key=lambda x: x["ativo"] is not True)
    return out


# Só leitura. Os três trechos da chave são parâmetros; o restante do SQL é constante.
_TITULARES_SEPULTURA = text("""
SELECT dados FROM bronze.registros
WHERE id IN (
    SELECT max(id) FROM bronze.registros
    WHERE sistema_origem = 'CEMITERIO' AND tabela = 'Titulares'
      AND quarentena IS NULL AND ambiente_origem = 'producao'
    GROUP BY id_origem)
  AND btrim(coalesce(dados->>'Cod_Cemiterio', '')) = :cod
  AND btrim(coalesce(dados->>'Quadra', '')) = :quadra
  AND btrim(coalesce(dados->>'Sepultura', '')) = :sepultura
""")


def titulares_sepultura(db: Session, cod: str, quadra: str, sepultura: str) -> list[dict]:
    """Todos os titulares da sepultura (ativos e inativos), o atual (Ativo='S') primeiro.

    A resposta tem somente nome, situação (Ativo/Inativo) e o indicador de atual: sem CPF, documento,
    endereço, Inclusao ou motivo.
    """
    cod_s, quadra_s, sep_s = (str(v or "").strip() for v in (cod, quadra, sepultura))
    if not cod_s or not sep_s or len(cod_s) > 40 or len(quadra_s) > 40 or len(sep_s) > 40:
        return []
    itens = []
    for (dados,) in db.execute(_TITULARES_SEPULTURA, {"cod": cod_s, "quadra": quadra_s, "sepultura": sep_s}):
        d = dados or {}
        atual = str(d.get("Ativo") or "").strip().upper() == "S"
        itens.append({"nome": _txt(d.get("Titular")) or "Nome não informado",
                      "situacao": "Ativo" if atual else "Inativo", "atual": atual})
    itens.sort(key=lambda x: (not x["atual"], x["nome"].casefold()))
    return itens
