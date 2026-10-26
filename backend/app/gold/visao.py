"""Gold: visão por cidadão e domínio para o futuro Portal do Cidadão.
Quando as fontes divergem, a gold mostra o valor da fonte com a data de atualização mais recente
(as demais versões continuam na silver/bronze). Cada valor guarda fonte e data. CPF não vai para a gold."""
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ingestion.mapeamentos import canonico
from app.models.camadas import Cidadao, CidadaoSistemas, DadoGold, RegistroBronze, SistemaSilver, Vinculo
from app.util.datas import normaliza_data_iso

DOMINIO_CAMPO = {"nome": "identificacao", "data_nascimento": "identificacao", "nome_mae": "identificacao",
                 "nome_pai": "identificacao", "sexo": "identificacao", "data_obito": "identificacao",
                 "endereco": "endereco"}
CAMPOS_DATA = {"data_nascimento", "data_obito"}
FORA_DA_GOLD = {"cpf", "cns", "atualizado_em", "chave_pessoa", "pista_normalizado", "pista_matricula_correta"}


def reconstruir(db: Session) -> dict[str, int]:
    db.execute(delete(DadoGold))
    db.execute(delete(CidadaoSistemas))
    dominios = {s.codigo: s.dominio or "outros" for s in db.scalars(select(SistemaSilver))}
    regs = {r.id: r for r in db.scalars(select(RegistroBronze).where(RegistroBronze.id.in_(select(Vinculo.bronze_id))))}
    melhor: dict[tuple[str, str], DadoGold] = {}
    sistemas: dict[str, set[str]] = {}
    for v in db.scalars(select(Vinculo).order_by(Vinculo.id)):
        r = regs[v.bronze_id]
        sistemas.setdefault(v.cidadao_id, set()).add(v.sistema)
        c = canonico(r.sistema_origem, r.tabela, r.dados)
        for campo, valor in c.items():
            if campo in CAMPOS_DATA:
                valor = normaliza_data_iso(valor)
            if campo in FORA_DA_GOLD or valor is None:
                continue
            k = (v.cidadao_id, campo)
            atual = melhor.get(k)
            if atual is None or (v.fonte_atualizado_em and (atual.fonte_atualizado_em is None or v.fonte_atualizado_em > atual.fonte_atualizado_em)):
                melhor[k] = DadoGold(cidadao_id=v.cidadao_id, dominio=DOMINIO_CAMPO.get(campo, "outros"), campo=campo,
                                     valor=str(valor), sistema=v.sistema, fonte_atualizado_em=v.fonte_atualizado_em,
                                     mais_recente=True)
        # presença do cidadão no domínio do sistema
        k = (v.cidadao_id, f"cadastro_{v.sistema}")
        melhor[k] = DadoGold(cidadao_id=v.cidadao_id, dominio=dominios.get(v.sistema, "outros"), campo="cadastro",
                             valor=f"{r.tabela}:{v.chave_origem}", sistema=v.sistema,
                             fonte_atualizado_em=v.fonte_atualizado_em, mais_recente=True)
    for c in db.scalars(select(Cidadao).where(Cidadao.falecido.is_(True))):
        sis = (c.obito_fonte or "CEMITERIO").split(".")[0]
        melhor[(c.cidadao_id, "falecido")] = DadoGold(cidadao_id=c.cidadao_id, dominio="identificacao", campo="falecido",
                                                      valor="sim (confirmado)", sistema=sis, fonte_atualizado_em=None,
                                                      mais_recente=True)
    db.add_all(melhor.values())
    for c in db.scalars(select(Cidadao)):
        sis = sorted(sistemas.get(c.cidadao_id, set()))
        db.add(CidadaoSistemas(cidadao_id=c.cidadao_id, nome=c.nome, sistemas=sis,
                               dominios=sorted({dominios.get(s, "outros") for s in sis}), atualizado_em=c.atualizado_em))
    db.flush()
    return {"dados": len(melhor)}
