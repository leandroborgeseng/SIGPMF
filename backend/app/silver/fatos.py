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

    n_at = _grava(db, Atendimento, (_atend(io, d, cid_mat.get) for io, d in _ultimas(db, "SAUDE", "Consultas")))
    n_ag = _grava(db, Agendamento, (_agend(io, d, cid_mat.get) for io, d in _ultimas(db, "SAUDE", "Agendamento_Consulta")))
    n_va = _grava(db, Vacinacao, (_vac(io, d, cid_mat.get, dims) for io, d in _ultimas(db, "SAUDE", "Vacina")))
    n_ev = _grava(db, Evento, _eventos(db, unidades, dims))
    return {"unidades": n_uni, "atendimentos": n_at, "agendamentos": n_ag, "vacinacoes": n_va, "eventos": n_ev}


def _mat(v):
    x = _i(v)
    return None if x is None else str(x)


def _atend(io, d, cid_de) -> Atendimento:
    m = _mat(d.get("Cod_Paciente"))
    return Atendimento(chave_origem=io[:200], matricula=m, cidadao_id=cid_de(m), data=_d(d.get("Dt_Atend")),
                       unidade=_i(d.get("Cod_Ubs")), especialidade=_i(d.get("Codigo_Especialidade")))


def _agend(io, d, cid_de) -> Agendamento:
    m = _mat(d.get("Cod_Pac"))
    cons, marc = _d(d.get("Dt_Cons")), _d(d.get("DataHoraAgendamento") or d.get("Data_Hora_SIGS"))
    espera = (cons - marc).days if cons and marc and cons >= marc else None
    return Agendamento(chave_origem=io[:200], matricula=m, cidadao_id=cid_de(m), data_consulta=cons,
                       data_marcacao=marc, dias_espera=espera, unidade=_i(d.get("Cod_Ubs")),
                       especialidade=_i(d.get("Cod_Espec")))


def _vac(io, d, cid_de, dims) -> Vacinacao:
    m = _mat(d.get("Matricula"))
    nome, dose = dims["vacinas"].get(_i(d.get("Cod_Vacina")), (None, None))
    return Vacinacao(chave_origem=io[:200], matricula=m, cidadao_id=cid_de(m), data=_d(d.get("Dt_Vacina")),
                     unidade=_i(d.get("Cod_Unidade")), vacina=nome or f"Vacina {d.get('Cod_Vacina')}", dose=dose)


FATOS = {  # tipo da pendência -> (tabela da bronze, modelo, construtor)
    "atend": ("Consultas", Atendimento, lambda io, d, c, dims: _atend(io, d, c)),
    "agend": ("Agendamento_Consulta", Agendamento, lambda io, d, c, dims: _agend(io, d, c)),
    "vac": ("Vacina", Vacinacao, _vac),
}


def recalcula_fatos(db: Session, tipo: str, chaves: set) -> set:
    """Parte 2: recalcula os fatos `tipo` destas chaves (DELETE + INSERT por chave). Devolve os cidadãos afetados."""
    tabela, model, faz = FATOS[tipo]
    if not chaves:
        return set()
    afetados = set(db.scalars(select(model.cidadao_id).where(model.chave_origem.in_(chaves),
                                                              model.cidadao_id.is_not(None))))
    db.execute(delete(model).where(model.chave_origem.in_(chaves)))
    linhas = list(_ultimas(db, "SAUDE", tabela, chaves=chaves))
    mats = set()
    for _io, d in linhas:
        mats |= {_mat(d.get(k)) for k in ("Cod_Paciente", "Cod_Pac", "Matricula")}
    mats.discard(None)
    cid_mat = {}
    if mats:
        cid_mat = {a: b for a, b in db.execute(select(Vinculo.chave_origem, Vinculo.cidadao_id).where(
            Vinculo.sistema == "SAUDE", Vinculo.tabela == "pacientes", Vinculo.chave_origem.in_(list(mats))))}
    dims = nomes(db) if tipo == "vac" else None
    novos = [faz(io, d, cid_mat.get, dims) for io, d in linhas]
    _grava(db, model, novos)
    return afetados | {x.cidadao_id for x in novos if x.cidadao_id}


def chaves_por_matricula(db: Session, mats: set) -> dict[str, set]:
    """Fatos ligados às matrículas (a troca de cidadão de uma matrícula muda o cidadão desses fatos)."""
    out = {}
    for tipo, (_t, model, _f) in FATOS.items():
        out[tipo] = set(db.scalars(select(model.chave_origem).where(model.matricula.in_(list(mats))))) if mats else set()
    return out


def recalcula_eventos(db: Session, cids: set) -> int:
    """Parte 2: linha do tempo só destes cidadãos (mesmas regras do _eventos)."""
    cids = {c for c in cids if c}
    if not cids:
        return 0
    db.execute(delete(Evento).where(Evento.cidadao_id.in_(cids)))
    unidades = {u.codigo: u for u in db.scalars(select(UnidadeSaude))}
    return _grava(db, Evento, _eventos(db, unidades, nomes(db), cids))


def _eventos(db: Session, unidades: dict, dims: dict, cids: set | None = None):
    hoje = date.today()
    nome_uni = {k: (u.nome or f"Unidade {k}") for k, u in unidades.items()}

    def so(model):
        return (model.cidadao_id.in_(list(cids)),) if cids is not None else ()
    for a in db.scalars(select(Atendimento).where(Atendimento.cidadao_id.is_not(None), Atendimento.data.is_not(None),
                                                  *so(Atendimento))):
        esp = dims["especialidades"].get(a.especialidade)
        yield Evento(cidadao_id=a.cidadao_id, data=a.data, area="saude", tipo="consulta",
                     titulo=f"Atendimento{' · ' + esp if esp else ''} · {nome_uni.get(a.unidade, 'unidade de saúde')}"[:300],
                     fonte="Saúde")
    for g in db.scalars(select(Agendamento).where(Agendamento.cidadao_id.is_not(None), Agendamento.data_consulta >= hoje,
                                                  *so(Agendamento))):
        esp = dims["especialidades"].get(g.especialidade)
        yield Evento(cidadao_id=g.cidadao_id, data=g.data_consulta, area="saude", tipo="agendamento",
                     titulo=f"Consulta agendada{' · ' + esp if esp else ''} · {nome_uni.get(g.unidade, 'unidade de saúde')}"[:300],
                     fonte="Saúde")
    for v in db.scalars(select(Vacinacao).where(Vacinacao.cidadao_id.is_not(None), Vacinacao.data.is_not(None), *so(Vacinacao))):
        yield Evento(cidadao_id=v.cidadao_id, data=v.data, area="saude", tipo="vacina",
                     titulo=f"Vacina {v.vacina}{' · ' + v.dose if v.dose else ''}"[:300], fonte="Saúde")
    for p in db.scalars(select(ImovelPossuidor).where(ImovelPossuidor.cidadao_id.is_not(None), ImovelPossuidor.desde.is_not(None),
                                                      *so(ImovelPossuidor))):
        yield Evento(cidadao_id=p.cidadao_id, data=p.desde.date(), area="impostos", tipo="posse",
                     titulo=f"Passou a constar como possuidor do imóvel {p.codi}", fonte="IPTU")
    for x in db.scalars(select(Lancamento).where(Lancamento.cidadao_id.is_not(None), Lancamento.vencimento.is_not(None),
                                                 *so(Lancamento))):
        trib = NOMES_TRIBUTO.get(x.tributo or "", "Tributo")
        if x.pago and x.pagamento:
            yield Evento(cidadao_id=x.cidadao_id, data=x.pagamento, area="impostos", tipo="pagamento",
                         titulo=f"{trib} {x.exercicio} · parcela {x.parcela} paga", fonte="Financeiro")
        elif not x.pago and x.vencimento < hoje:
            yield Evento(cidadao_id=x.cidadao_id, data=x.vencimento, area="impostos", tipo="vencido",
                         titulo=f"{trib} {x.exercicio} · parcela {x.parcela} venceu sem pagamento", fonte="Financeiro")
    for d in db.scalars(select(DividaAtiva).where(DividaAtiva.cidadao_id.is_not(None), DividaAtiva.inscricao.is_not(None),
                                                  *so(DividaAtiva))):
        yield Evento(cidadao_id=d.cidadao_id, data=d.inscricao, area="impostos", tipo="divida",
                     titulo=f"Débito de {d.ano} inscrito em dívida ativa" + (" (extinto)" if d.extinta else ""), fonte="Dívida ativa")
