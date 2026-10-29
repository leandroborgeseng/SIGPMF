"""Prata da educação a partir da bronze (GESTAOESCOLAR + CORESSO). Idempotente, só upsert: nunca DELETE.

GE6 v2: aluno → pessoa do CoreSSO pela junção direta `ACA_Aluno.pes_id = PES_Pessoa.pes_id`.
- GE6a: CPF (documento de classificação 1) com DV válido → vínculo automático, peso 1.0.
- GE6b: sem CPF, nome + nascimento + nome da mãe batendo com um cidadão já conhecido (score >= limiar) → revisão
  humana (silver.correlacoes_pendentes), nunca vínculo automático.
- GE6c: nada casou → quarentena, com motivo (no aluno e na linha da bronze).
- GE6d: NIS = classificações 3/4 (guardado, não vincula sozinho); certidão (tipo 1) e endereço principal do CoreSSO.
- Mãe: `pes_idFiliacaoMae` da pessoa; sem ela, o responsável com `tra_id = 1` (Filiação 1).
GE9: situação 1 = ativo; situação 3 = exclusão lógica (`excluido`/`excluida`, status `excluido`, some da ficha);
qualquer outra situação (4, 5, 8, 12, 13...) = inativo: continua vinculado e aparece na ficha como histórico.
Unidade (ESC_UnidadeEscola): `uni_descricao` é o nome da unidade; `uni_situacao` 3 marca a unidade como excluída.
Os logs só têm contagens (nenhum nome, CPF ou id de aluno).
"""
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.identity import correlacao as corr
from app.identity import cpf as cpfmod
from app.models.camadas import (
    Cidadao,
    CorrelacaoPendente,
    EscolaAluno,
    EscolaMatricula,
    EscolaResponsavel,
    Identificador,
    Municipe,
    RegistroBronze,
)

ESCOLAR, PESSOAS = "GESTAOESCOLAR", "CORESSO"
SITUACAO_ATIVA, SITUACAO_EXCLUIDA = 1, 3
TRA_MAE = 1  # decisão do DBA (29/10): Filiação 1 = mãe
CLASS_CPF, CLASS_NIS = 1, (3, 4)
MOTIVO_REVISAO = "revisao_nome_nascimento_mae"
MOTIVO_SEM_CASAMENTO = "sem_cpf_sem_casamento"
MOTIVO_SEM_PESSOA = "pessoa_ausente_no_coresso"
MOTIVO_CPF_INVALIDO = "cpf_invalido_sem_casamento"


def _ultimas(db: Session, sistema: str, tabela: str) -> list[tuple[int, dict]]:
    sub = (select(func.max(RegistroBronze.id)).where(RegistroBronze.sistema_origem == sistema,
                                                     RegistroBronze.tabela == tabela)
           .group_by(RegistroBronze.id_origem))
    return [(i, d) for i, d in db.execute(select(RegistroBronze.id, RegistroBronze.dados)
                                          .where(RegistroBronze.id.in_(sub)).order_by(RegistroBronze.id))]


def _int(v) -> int | None:
    try:
        return None if v is None or v == "" else int(v)
    except (TypeError, ValueError):
        return None


def _data(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _dt(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def _pid(v) -> str | None:
    return str(v).lower() if v else None


def _digitos(v) -> str | None:
    d = cpfmod.normaliza(v if not isinstance(v, int) else str(v))
    return d


def _candidatos(db: Session, pessoa: dict, limiar: float) -> list[tuple[str, float, list[str]]]:
    """Cidadãos já conhecidos (munícipes com cidadao_id) com o mesmo nascimento; score nome + nascimento + mãe."""
    nasc = pessoa.get("data_nascimento")
    if not nasc:
        return []
    p = corr.Parametros()
    out = []
    for m in db.scalars(select(Municipe).where(Municipe.data_nascimento == nasc, Municipe.cidadao_id.is_not(None))):
        s, attrs = corr.score(pessoa, {"nome": m.nome, "data_nascimento": m.data_nascimento, "nome_mae": m.nome_mae}, p)
        if s >= limiar and {"nome", "data_nascimento", "nome_mae"} <= set(attrs):
            out.append((m.cidadao_id, s, attrs))
    return sorted(out, key=lambda x: -x[1])


def processa(db: Session, agora: datetime | None = None) -> dict[str, int]:
    agora = agora or datetime.now(UTC)
    limiar = corr.Parametros().limiar_revisao
    pessoas = {_pid(d.get("pes_id")): d for _i, d in _ultimas(db, PESSOAS, "PES_Pessoa")}
    docs: dict[str, dict[str, str]] = {}
    for _i, d in _ultimas(db, PESSOAS, "PES_PessoaDocumento"):
        if _int(d.get("psd_situacao")) == SITUACAO_EXCLUIDA:
            continue
        # classe derivada do catálogo na leitura (plano A/B, consultas.classe_do_tipo); bronze antiga: tdo_classificacao
        cl = _int(d.get("tdo_classe")) if d.get("tdo_classe") is not None else _int(d.get("tdo_classificacao"))
        num, pid = _digitos(d.get("psd_numero")), _pid(d.get("pes_id"))
        if cl == CLASS_CPF and num:
            docs.setdefault(pid, {})["cpf"] = num
        elif cl in CLASS_NIS and num and len(num) == 11:
            docs.setdefault(pid, {}).setdefault("nis", num)
    certidoes = {}
    for _i, d in _ultimas(db, PESSOAS, "PES_CertidaoCivil"):
        if _int(d.get("ctc_tipo")) == 1 and _int(d.get("ctc_situacao")) != SITUACAO_EXCLUIDA and d.get("ctc_matricula"):
            certidoes[_pid(d.get("pes_id"))] = str(d["ctc_matricula"])[:40]
    enderecos = {}
    for _i, d in _ultimas(db, PESSOAS, "PES_PessoaEndereco"):
        if _int(d.get("pse_situacao")) != SITUACAO_EXCLUIDA:
            enderecos[_pid(d.get("pes_id"))] = d
    tipos = {_int(d.get("tra_id")): d.get("tra_nome") for _i, d in _ultimas(db, ESCOLAR, "ACA_TipoResponsavelAluno")}
    resp_rows = _ultimas(db, ESCOLAR, "ACA_AlunoResponsavel")
    mae_resp = {}
    for _i, d in resp_rows:
        if _int(d.get("tra_id")) == TRA_MAE and _int(d.get("alr_situacao")) != SITUACAO_EXCLUIDA and d.get("pes_id"):
            mae_resp.setdefault(_int(d.get("alu_id")), _pid(d.get("pes_id")))

    def nome_de(pid):
        return (pessoas.get(pid) or {}).get("pes_nome")

    c = {"alunos": 0, "vinculados": 0, "revisao": 0, "quarentena": 0, "excluidos": 0, "inativos": 0, "matriculas": 0,
         "matriculas_atuais": 0, "matriculas_excluidas": 0, "responsaveis": 0}
    for bronze_id, d in _ultimas(db, ESCOLAR, "ACA_Aluno"):
        alu_id, pid = _int(d.get("alu_id")), _pid(d.get("pes_id"))
        if alu_id is None:
            continue
        pes = pessoas.get(pid)
        situacao = _int(d.get("alu_situacao"))
        excluido = situacao == SITUACAO_EXCLUIDA or (pes is not None and _int(pes.get("pes_situacao")) == SITUACAO_EXCLUIDA)
        mae_id = _pid((pes or {}).get("pes_idFiliacaoMae")) or mae_resp.get(alu_id)
        doc = docs.get(pid, {})
        cpf = doc.get("cpf") if doc.get("cpf") and cpfmod.valido(doc.get("cpf")) else None
        end = enderecos.get(pid) or {}
        base = {"nome": (pes or {}).get("pes_nome"), "data_nascimento": _data((pes or {}).get("pes_dataNascimento")),
                "nome_mae": nome_de(mae_id)}
        atual = db.get(EscolaAluno, alu_id)
        status, motivo, cid, conf, attrs = "quarentena", MOTIVO_SEM_CASAMENTO, None, None, []
        if excluido:
            # situação 3 (exclusão lógica): não vira vínculo novo; se já existia, mantém o cidadao_id, mas sai da ficha.
            # Não confundir com inativo (outras situações), que segue vinculado e visível como histórico.
            status, motivo = "excluido", "situacao_excluida"
            cid, conf = (atual.cidadao_id, atual.confianca) if atual else (None, None)
        elif pes is None:
            motivo = MOTIVO_SEM_PESSOA
        elif cpf:
            status, motivo, cid, conf, attrs = "vinculado", None, corr.cidadao_id_por_cpf(cpf), 1.0, ["cpf"]
            if db.get(Cidadao, cid) is None and (cid_id := db.scalar(select(Identificador.cidadao_id).where(
                    Identificador.tipo == "cpf", Identificador.valor == cpf))):
                cid = cid_id
        else:
            cands = _candidatos(db, base, limiar)
            if cands:
                status, motivo, conf, attrs = "revisao", MOTIVO_REVISAO, cands[0][1], cands[0][2]
                ins = insert(CorrelacaoPendente).values(
                    sistema=ESCOLAR, tabela="ACA_Aluno", chave_origem=str(alu_id), bronze_id=bronze_id, motivo=MOTIVO_REVISAO,
                    nome=None, candidatos=[x[0] for x in cands[:5]], detalhe={"score": cands[0][1], "atributos": cands[0][2]},
                    status="pendente")
                db.execute(ins.on_conflict_do_update(index_elements=["sistema", "tabela", "chave_origem"],
                                                     set_={"candidatos": ins.excluded.candidatos,
                                                           "detalhe": ins.excluded.detalhe,
                                                           "bronze_id": ins.excluded.bronze_id}))
            elif doc.get("cpf"):
                motivo = MOTIVO_CPF_INVALIDO
        if status == "quarentena":
            db.execute(update(RegistroBronze).where(RegistroBronze.id == bronze_id).values(
                quarentena=motivo, quarentena_campos=["pes_id"] if motivo == MOTIVO_SEM_PESSOA else ["cpf"]))
        else:  # casou depois (ex.: CPF chegou): sai da quarentena
            db.execute(update(RegistroBronze).where(RegistroBronze.id == bronze_id,
                                                    RegistroBronze.quarentena.is_not(None)).values(
                quarentena=None, quarentena_campos=None))
        valores: dict[str, Any] = {
            "pes_id": pid, "situacao": situacao, "excluido": excluido, "nome": base["nome"],
            "nome_social": (pes or {}).get("pes_nomeSocial"), "data_nascimento": base["data_nascimento"],
            "nome_mae": base["nome_mae"], "cpf": cpf, "nis": doc.get("nis"), "certidao": certidoes.get(pid),
            "cep": _digitos(end.get("end_cep")), "logradouro": end.get("end_logradouro"),
            "numero": None if end.get("pse_numero") is None else str(end.get("pse_numero"))[:30],
            "complemento": end.get("pse_complemento"), "bairro": end.get("end_bairro"), "cidadao_id": cid,
            "confianca": conf, "status_vinculo": status, "motivo": motivo, "atributos": attrs, "atualizado_em": agora}
        ins = insert(EscolaAluno).values(alu_id=alu_id, **valores)
        db.execute(ins.on_conflict_do_update(index_elements=["alu_id"], set_=valores))
        c["alunos"] += 1
        c[{"vinculado": "vinculados", "revisao": "revisao", "quarentena": "quarentena", "excluido": "excluidos"}[status]] += 1
        c["inativos"] += int(not excluido and situacao != SITUACAO_ATIVA)

    # ---- matrículas: AlunoCurriculo -> MatriculaTurma -> TurmaCurriculo -> Turma -> UnidadeEscola -> Escola ----
    curr = {(_int(d.get("alu_id")), _int(d.get("alc_id"))): d for _i, d in _ultimas(db, ESCOLAR, "ACA_AlunoCurriculo")}
    turmas = {_int(d.get("tur_id")): d for _i, d in _ultimas(db, ESCOLAR, "TUR_Turma")}
    unidades = {(_int(d.get("esc_id")), _int(d.get("uni_id"))): d for _i, d in _ultimas(db, ESCOLAR, "ESC_UnidadeEscola")}
    escolas = {_int(d.get("esc_id")): d for _i, d in _ultimas(db, ESCOLAR, "ESC_Escola")}
    for _i, d in _ultimas(db, ESCOLAR, "MTR_MatriculaTurma"):
        alu_id, mtu_id = _int(d.get("alu_id")), _int(d.get("mtu_id"))
        if alu_id is None or mtu_id is None:
            continue
        alc = curr.get((alu_id, _int(d.get("alc_id")))) or {}
        tur = turmas.get(_int(d.get("tur_id"))) or {}
        esc_id = _int(alc.get("esc_id")) or _int(tur.get("esc_id"))
        uni_id = _int(alc.get("uni_id")) or _int(tur.get("uni_id"))
        if unidades and (esc_id, uni_id) not in unidades:
            esc_id = _int(tur.get("esc_id"))
        esc = escolas.get(esc_id) or {}
        uni = unidades.get((esc_id, uni_id)) or {}
        uni_sit = _int(uni.get("uni_situacao"))
        uni_nome = (str(uni.get("uni_descricao")).strip() or None) if uni.get("uni_descricao") is not None else None
        sit = _int(d.get("mtu_situacao"))
        alc_sit = _int(alc.get("alc_situacao")) if alc else None
        excluida = sit == SITUACAO_EXCLUIDA or alc_sit == SITUACAO_EXCLUIDA
        atual = sit == SITUACAO_ATIVA and not excluida
        valores = {"alc_id": _int(d.get("alc_id")), "matricula": alc.get("alc_matricula"), "tur_id": _int(d.get("tur_id")),
                   "turma": tur.get("tur_codigo"), "esc_id": esc_id, "escola": esc.get("esc_nome"),
                   "uni_id": uni_id, "unidade": uni_nome[:200] if uni_nome else None,
                   "unidade_situacao": uni_sit, "unidade_excluida": uni_sit == SITUACAO_EXCLUIDA,
                   "escola_inep": None if esc.get("esc_codigoInep") is None else str(esc.get("esc_codigoInep")),
                   "situacao": sit, "atual": atual, "excluida": excluida, "data_alteracao": _dt(d.get("mtu_dataAlteracao"))}
        ins = insert(EscolaMatricula).values(alu_id=alu_id, mtu_id=mtu_id, **valores)
        db.execute(ins.on_conflict_do_update(index_elements=["alu_id", "mtu_id"], set_=valores))
        c["matriculas"] += 1
        c["matriculas_atuais"] += int(atual)
        c["matriculas_excluidas"] += int(excluida)

    for _i, d in resp_rows:
        alu_id, alr_id = _int(d.get("alu_id")), _int(d.get("alr_id"))
        if alu_id is None or alr_id is None:
            continue
        pid = _pid(d.get("pes_id"))
        rcpf = docs.get(pid, {}).get("cpf")
        sit = _int(d.get("alr_situacao"))
        valores = {"tra_id": _int(d.get("tra_id")), "tipo": tipos.get(_int(d.get("tra_id"))), "pes_id": pid,
                   "nome": nome_de(pid), "cpf": rcpf if rcpf and cpfmod.valido(rcpf) else None,
                   "principal": None if d.get("alr_principal") is None else bool(d.get("alr_principal")),
                   "situacao": sit, "excluido": sit == SITUACAO_EXCLUIDA}
        ins = insert(EscolaResponsavel).values(alu_id=alu_id, alr_id=alr_id, **valores)
        db.execute(ins.on_conflict_do_update(index_elements=["alu_id", "alr_id"], set_=valores))
        c["responsaveis"] += 1
    db.flush()
    return c
