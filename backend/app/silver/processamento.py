"""Silver: reconstrói o cadastro correlacionado a partir da versão mais recente de cada registro bronze.
Determinístico: com o mesmo bronze, o resultado é o mesmo (idempotência)."""
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.identity import correlacao as corr
from app.identity.cpf import mascara, normaliza, valido
from app.ingestion.mapeamentos import MAPEAMENTOS, canonico
from app.models.camadas import (
    Cidadao,
    CorrelacaoPendente,
    Endereco,
    Identificador,
    Inconsistencia,
    RegistroBronze,
    SistemaSilver,
    Vinculo,
)
from app.models.controle import Parametro, Sistema


def parametros(db: Session) -> corr.Parametros:
    v = {p.chave: float(p.valor) for p in db.scalars(select(Parametro))}
    d = corr.Parametros()
    return corr.Parametros(v.get("correlacao_limiar_revisao", d.limiar_revisao), v.get("correlacao_peso_nome", d.peso_nome),
                           v.get("correlacao_peso_nascimento", d.peso_nascimento), v.get("correlacao_peso_mae", d.peso_mae))


def _ultimas_versoes(db: Session) -> list[RegistroBronze]:
    sub = select(func.max(RegistroBronze.id)).group_by(
        RegistroBronze.sistema_origem, RegistroBronze.tabela, RegistroBronze.id_origem)
    return list(db.scalars(select(RegistroBronze).where(RegistroBronze.id.in_(sub)).order_by(RegistroBronze.id)))


def _data(v: str | None) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def reconstruir(db: Session) -> dict[str, int]:
    p = parametros(db)
    for m in (Endereco, Vinculo, Identificador, CorrelacaoPendente, Inconsistencia, Cidadao, SistemaSilver):
        db.execute(delete(m))
    for s in db.scalars(select(Sistema)):
        db.add(SistemaSilver(codigo=s.codigo, nome=s.nome, dominio=s.dominio))

    cid_dados: dict[str, dict] = {}       # cidadao_id -> dados canônicos de referência
    cidadaos: dict[str, Cidadao] = {}
    ids: list[Identificador] = []
    vinculos: list[tuple[RegistroBronze, dict, str, str, float, list[str]]] = []
    pend: list[CorrelacaoPendente] = []
    inc: list[Inconsistencia] = []
    sem_cpf: list[tuple[RegistroBronze, dict]] = []

    def novo(cid: str, r: RegistroBronze, c: dict) -> None:
        cidadaos[cid] = Cidadao(cidadao_id=cid, nome=c.get("nome") or "", data_nascimento=_data(c.get("data_nascimento")),
                                atualizado_em=r.atualizado_origem_em)
        cid_dados[cid] = c

    regs = [(r, canonico(r.sistema_origem, r.tabela, r.dados)) for r in _ultimas_versoes(db)]
    for r, c in regs:  # 1º passe: CPF
        cpf = normaliza(c.get("cpf"))
        if cpf and valido(cpf):
            cid = corr.cidadao_id_por_cpf(cpf)
            if cid not in cidadaos:
                novo(cid, r, c)
                ids.append(Identificador(cidadao_id=cid, tipo="cpf", valor=cpf))
            elif cid_dados[cid].get("data_nascimento") != c.get("data_nascimento"):
                pend.append(CorrelacaoPendente(sistema=r.sistema_origem, tabela=r.tabela, chave_origem=r.id_origem,
                                               bronze_id=r.id, motivo="ambiguo", nome=c.get("nome"), candidatos=[cid]))
                inc.append(Inconsistencia(cidadao_id=cid, sistema=r.sistema_origem, tabela=r.tabela, chave_origem=r.id_origem,
                                          tipo="cpf_nascimento_divergente", campo="data_nascimento",
                                          detalhe=f"Mesmo CPF {mascara(cpf)} com data de nascimento divergente"))
                continue
            elif r.atualizado_origem_em and (cidadaos[cid].atualizado_em is None or r.atualizado_origem_em > cidadaos[cid].atualizado_em):
                cidadaos[cid].nome, cidadaos[cid].atualizado_em = c.get("nome") or "", r.atualizado_origem_em  # mais recente vence
            vinculos.append((r, c, cid, "cpf", 1.0, ["cpf"]))
        else:
            if cpf:
                inc.append(Inconsistencia(sistema=r.sistema_origem, tabela=r.tabela, chave_origem=r.id_origem,
                                          tipo="cpf_invalido", campo="cpf", detalhe=f"CPF inválido {mascara(cpf)}"))
            sem_cpf.append((r, c))

    for r, c in sem_cpf:  # 2º passe: atributos
        melhor, mcid, mattrs = 0.0, None, []
        for cid, ref in cid_dados.items():
            sc, attrs = corr.score(c, ref, p)
            if sc > melhor:
                melhor, mcid, mattrs = sc, cid, attrs
        if mcid and melhor >= p.limiar_revisao:
            pend.append(CorrelacaoPendente(sistema=r.sistema_origem, tabela=r.tabela, chave_origem=r.id_origem, bronze_id=r.id,
                                           motivo="revisao", nome=c.get("nome"), candidatos=[mcid]))
            continue
        cid = corr.cidadao_id_por_origem(r.sistema_origem, r.tabela, r.id_origem)
        novo(cid, r, c)
        vinculos.append((r, c, cid, "novo_sem_cpf", round(melhor, 4), mattrs))

    db.add_all(cidadaos.values())
    db.flush()
    db.add_all(ids + pend + inc)
    enderecos: list[Endereco] = []
    for r, c, cid, metodo, conf, attrs in vinculos:
        db.add(Vinculo(cidadao_id=cid, sistema=r.sistema_origem, tabela=r.tabela, chave_origem=r.id_origem, bronze_id=r.id,
                       metodo=metodo, confianca=conf, atributos=attrs, fonte_atualizado_em=r.atualizado_origem_em))
        if c.get("endereco"):
            enderecos.append(Endereco(cidadao_id=cid, logradouro=c["endereco"], sistema=r.sistema_origem,
                                      fonte_atualizado_em=r.atualizado_origem_em))
    ultimo: dict[str, Endereco] = {}
    for e in enderecos:
        u = ultimo.get(e.cidadao_id)
        if u is None or (e.fonte_atualizado_em and (u.fonte_atualizado_em is None or e.fonte_atualizado_em > u.fonte_atualizado_em)):
            ultimo[e.cidadao_id] = e
    for e in ultimo.values():
        e.mais_recente = True
    db.add_all(enderecos)
    db.flush()
    return {"cidadaos": len(cidadaos), "vinculos": len(vinculos), "pendentes": len(pend), "mapeados": len(MAPEAMENTOS)}
