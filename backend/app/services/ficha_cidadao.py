"""Ficha do cidadão: túmulos e sepultamento (cemitério) e o que cada área da ficha já tem carregado.

A ficha mostra todas as áreas, inclusive as vazias. "Não tem registro" só vale quando a base foi carregada; por isso
`fontes` diz, sem nenhum dado de pessoa, quais tabelas têm linhas e quais bases ainda não fazem parte do SIGPMF.

Cemitério: vem dos vínculos do cidadão (titular, autorizado e leilão por CPF válido; sepultamento só com óbito
confirmado por pessoa). Nome de terceiros, endereço e documento da linha de origem nunca saem daqui.
"""
from sqlalchemy import literal, select
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
NAO_INTEGRADAS = (("Servidor municipal (folha)", "DBSENIOR"), ("Assistência social", "CadastroUnico"),
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
        return {"cemiterio": nomes.get(_cod(cod)) or (f"Cemitério {str(cod).strip()}" if cod is not None else None),
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
