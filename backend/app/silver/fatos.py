"""Onda 3 (SA-F2/SA-F3): fatos da Saúde e linha do tempo do cidadão, reconstruídos da bronze no fim da prata.

- Atendimentos, agendamentos e vacinações SEM conteúdo clínico (a política nem traz essas colunas). O cidadão vem da
  matrícula do paciente já correlacionada (silver.vinculos de SAUDE.pacientes); sem vínculo, o fato fica só agregado.
  ❓ confirmar com a equipe da Saúde que Consultas.Cod_Paciente e Agendamento_Consulta.Cod_Pac são a Matricula.
- Linha do tempo (silver.eventos): um evento por fato ligado a cidadão — consulta, consulta agendada, vacina, posse
  de imóvel, lançamento pago/vencido e inscrição em dívida ativa. Só título e fonte; nada clínico, nenhum valor de CPF.
"""
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.camadas import (
    Agendamento,
    Atendimento,
    DividaAtiva,
    Evento,
    ImovelPossuidor,
    Lancamento,
    UnidadeSaude,
    Vacinacao,
    Vinculo,
)
from app.silver.tributario import _d, _grava, _i, _s, _ultimas

NOMES_TRIBUTO = {"001": "IPTU", "002": "ISS", "003": "Taxas"}


def nomes(db: Session) -> dict[str, dict]:
    """Dimensões lidas da bronze: unidades, vacinas e especialidades (código -> nome)."""
    vac = {_i(d.get("Cod_Vacina")): (_s(d.get("Vacina"), 60), _s(d.get("Dose"), 10)) for _io, d in _ultimas(db, "SAUDE", "Aux_Vacina")}
    esp = {_i(d.get("Codi_Espe")): _s(d.get("Desc_Espe"), 80) for _io, d in _ultimas(db, "SAUDE", "Aux_Especialidades")}
    return {"vacinas": vac, "especialidades": esp}


def reconstruir(db: Session) -> dict[str, int]:
    for m in (Evento, Vacinacao, Agendamento, Atendimento, UnidadeSaude):
        db.execute(delete(m))
    cid_mat = {k: c for k, c in db.execute(select(Vinculo.chave_origem, Vinculo.cidadao_id)
                                           .where(Vinculo.sistema == "SAUDE", Vinculo.tabela == "pacientes"))}
    dims = nomes(db)
    unidades = {}
    for _io, d in _ultimas(db, "SAUDE", "Unidades"):
        cod = _i(d.get("Codigo"))
        if cod is not None:
            unidades[cod] = UnidadeSaude(codigo=cod, nome=_s(d.get("Nome_Fantasia"), 250), tipo=_s(d.get("Tipo"), 5),
                                         ativa=str(d.get("Inativo_Ativo") or "A").upper() != "I", cd_bairro=_i(d.get("Cd_Bai")))
    n_uni = _grava(db, UnidadeSaude, unidades.values())

    def mat(v):
        x = _i(v)
        return None if x is None else str(x)

    def atendimentos():
        for io, d in _ultimas(db, "SAUDE", "Consultas"):
            m = mat(d.get("Cod_Paciente"))
            yield Atendimento(chave_origem=io[:200], matricula=m, cidadao_id=cid_mat.get(m), data=_d(d.get("Dt_Atend")),
                              unidade=_i(d.get("Cod_Ubs")), especialidade=_i(d.get("Codigo_Especialidade")))
    n_at = _grava(db, Atendimento, atendimentos())

    def agendamentos():
        for io, d in _ultimas(db, "SAUDE", "Agendamento_Consulta"):
            m = mat(d.get("Cod_Pac"))
            cons, marc = _d(d.get("Dt_Cons")), _d(d.get("DataHoraAgendamento") or d.get("Data_Hora_SIGS"))
            espera = (cons - marc).days if cons and marc and cons >= marc else None
            yield Agendamento(chave_origem=io[:200], matricula=m, cidadao_id=cid_mat.get(m), data_consulta=cons,
                              data_marcacao=marc, dias_espera=espera, unidade=_i(d.get("Cod_Ubs")),
                              especialidade=_i(d.get("Cod_Espec")))
    n_ag = _grava(db, Agendamento, agendamentos())

    def vacinas():
        for io, d in _ultimas(db, "SAUDE", "Vacina"):
            m = mat(d.get("Matricula"))
            nome, dose = dims["vacinas"].get(_i(d.get("Cod_Vacina")), (None, None))
            yield Vacinacao(chave_origem=io[:200], matricula=m, cidadao_id=cid_mat.get(m), data=_d(d.get("Dt_Vacina")),
                            unidade=_i(d.get("Cod_Unidade")), vacina=nome or f"Vacina {d.get('Cod_Vacina')}", dose=dose)
    n_va = _grava(db, Vacinacao, vacinas())
    n_ev = _grava(db, Evento, _eventos(db, unidades, dims))
    return {"unidades": n_uni, "atendimentos": n_at, "agendamentos": n_ag, "vacinacoes": n_va, "eventos": n_ev}


def _eventos(db: Session, unidades: dict, dims: dict):
    hoje = date.today()
    nome_uni = {k: (u.nome or f"Unidade {k}") for k, u in unidades.items()}
    for a in db.scalars(select(Atendimento).where(Atendimento.cidadao_id.is_not(None), Atendimento.data.is_not(None))):
        esp = dims["especialidades"].get(a.especialidade)
        yield Evento(cidadao_id=a.cidadao_id, data=a.data, area="saude", tipo="consulta",
                     titulo=f"Atendimento{' · ' + esp if esp else ''} · {nome_uni.get(a.unidade, 'unidade de saúde')}"[:300],
                     fonte="Saúde")
    for g in db.scalars(select(Agendamento).where(Agendamento.cidadao_id.is_not(None), Agendamento.data_consulta >= hoje)):
        esp = dims["especialidades"].get(g.especialidade)
        yield Evento(cidadao_id=g.cidadao_id, data=g.data_consulta, area="saude", tipo="agendamento",
                     titulo=f"Consulta agendada{' · ' + esp if esp else ''} · {nome_uni.get(g.unidade, 'unidade de saúde')}"[:300],
                     fonte="Saúde")
    for v in db.scalars(select(Vacinacao).where(Vacinacao.cidadao_id.is_not(None), Vacinacao.data.is_not(None))):
        yield Evento(cidadao_id=v.cidadao_id, data=v.data, area="saude", tipo="vacina",
                     titulo=f"Vacina {v.vacina}{' · ' + v.dose if v.dose else ''}"[:300], fonte="Saúde")
    for p in db.scalars(select(ImovelPossuidor).where(ImovelPossuidor.cidadao_id.is_not(None), ImovelPossuidor.desde.is_not(None))):
        yield Evento(cidadao_id=p.cidadao_id, data=p.desde.date(), area="impostos", tipo="posse",
                     titulo=f"Passou a constar como possuidor do imóvel {p.codi}", fonte="IPTU")
    for x in db.scalars(select(Lancamento).where(Lancamento.cidadao_id.is_not(None), Lancamento.vencimento.is_not(None))):
        trib = NOMES_TRIBUTO.get(x.tributo or "", "Tributo")
        if x.pago and x.pagamento:
            yield Evento(cidadao_id=x.cidadao_id, data=x.pagamento, area="impostos", tipo="pagamento",
                         titulo=f"{trib} {x.exercicio} · parcela {x.parcela} paga", fonte="Financeiro")
        elif not x.pago and x.vencimento < hoje:
            yield Evento(cidadao_id=x.cidadao_id, data=x.vencimento, area="impostos", tipo="vencido",
                         titulo=f"{trib} {x.exercicio} · parcela {x.parcela} venceu sem pagamento", fonte="Financeiro")
    for d in db.scalars(select(DividaAtiva).where(DividaAtiva.cidadao_id.is_not(None), DividaAtiva.inscricao.is_not(None))):
        yield Evento(cidadao_id=d.cidadao_id, data=d.inscricao, area="impostos", tipo="divida",
                     titulo=f"Débito de {d.ano} inscrito em dívida ativa" + (" (extinto)" if d.extinta else ""), fonte="Dívida ativa")
