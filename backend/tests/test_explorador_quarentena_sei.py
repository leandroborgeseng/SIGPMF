"""M1/B2 do PR #34: linha SEI reclassificada para restrito (quarentena='sei_reclassificado_restrito') não aparece em
nenhuma leitura da bronze do explorador (perfil, chaves, tabelas, validação, amostra, revelar, pendências).
Somente dados SINTÉTICOS ZZTESTE (sistema fictício ZZSEIQ)."""
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.explorador import servico as sv

S = "ZZSEIQ"
OCULTO_ID = "ZZCODINT-700"
OCULTO_VAL = "ZZTESTE-OCULTO-9000700"
OCULTO_CHAVE = "zz_so_no_oculto"


@pytest.fixture(scope="module")
def zzq(client):
    from app.core.db import SessionLocal
    db = SessionLocal()
    eid = db.execute(text("INSERT INTO controle.execucoes (tipo, sistema, status, finalizado_em) VALUES "
                          "('carga_demo_amostra', :s, 'sucesso', now()) RETURNING id"), {"s": S}).scalar()

    def ins(tabela, io, dados, quar=None):
        return db.execute(text("INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id, "
                               "quarentena) VALUES (:s, :t, :io, CAST(:d AS jsonb), :h, :e, :q) RETURNING id"),
                          {"s": S, "t": tabela, "io": io, "d": json.dumps(dados), "h": f"h-{tabela}-{io}", "e": eid,
                           "q": quar}).scalar()

    ids = {
        "v1": ins("Processos", "ZZTESTE-1", {"numero": "ZZTESTE-1", "tipo": "A"}),
        "v2": ins("Processos", "ZZTESTE-2", {"numero": "ZZTESTE-2", "tipo": "B"}),
        "oculto": ins("Processos", OCULTO_ID, {"numero": OCULTO_VAL, "tipo": OCULTO_VAL, OCULTO_CHAVE: OCULTO_VAL},
                      sv.QUAR_OCULTA),
        # (d) quarentena comum continua contando e aparece nas pendências
        "a1": ins("Andamentos", "ZZTESTE-A1", {"descricao": "ZZTESTE andamento"}),
        "a_quar": ins("Andamentos", "ZZTESTE-A2", {"descricao": "ZZTESTE andamento chave nula"}, "chave_nula"),
        "a_oculto": ins("Andamentos", "ZZCODINT-700-A", {"descricao": OCULTO_VAL}, sv.QUAR_OCULTA),
        # tabela que só tem linha oculta: nem a existência pode vazar
        "so_oculto": ins("SoOculto", "ZZCODINT-700-S", {"descricao": OCULTO_VAL}, sv.QUAR_OCULTA),
    }
    db.commit()
    db.close()
    return ids




def test_m1_perfil_conta_2_e_nao_expoe_oculto(db, zzq):
    p = sv.perfil(db, S, "bronze", "Processos")
    assert p["linhas"] == 2 and p["amostra_perfil"] == 2
    cols = {c["coluna"]: c for c in p["colunas"]}
    assert set(cols) == {"numero", "tipo"}
    assert cols["tipo"]["distintos"] == 2 and cols["tipo"]["preenchido_pct"] == 100.0
    assert OCULTO_VAL not in json.dumps(p, default=str)


def test_m1_chaves_bronze_omitem_chave_da_linha_oculta(db, zzq):
    assert sv.chaves_bronze(db, S, "Processos") == ["numero", "tipo"]


def test_m1_valida_tabela_bronze_ignora_linha_oculta(db, zzq):
    assert sv.valida_tabela_bronze(db, S, "Processos") == "Processos"
    with pytest.raises(HTTPException) as e:
        sv.valida_tabela_bronze(db, S, "SoOculto")
    assert e.value.status_code == 404


def test_m1_tabelas_conta_so_visiveis(db, zzq):
    t = {x["tabela"]: x["linhas"] for x in sv.tabelas(db, S, "bronze")["tabelas"]}
    assert t == {"Processos": 2, "Andamentos": 2}


def test_m1_quarentena_comum_continua_contando(db, zzq):
    assert sv.perfil(db, S, "bronze", "Andamentos")["linhas"] == 2
    a = sv.amostra(db, S, "bronze", "Andamentos", 1, {})
    assert sorted(x["quarentena"] or "" for x in a["linhas"]) == ["", "chave_nula"]


def test_m1_perfil_pela_api(client, tokens, zzq):
    r = client.post("/api/explorar/perfil", json={"conector": S, "camada": "bronze", "tabela": "Processos"},
                    headers=tokens["admin_lgpd"])
    assert r.status_code == 200, r.text
    assert r.json()["linhas"] == 2 and OCULTO_VAL not in r.text and OCULTO_CHAVE not in r.text


def test_b2_revelar_linha_reclassificada_devolve_none(db, zzq):
    assert sv.valor_original(db, S, "bronze", "Processos", zzq["v1"], "numero") == "ZZTESTE-1"
    assert sv.valor_original(db, S, "bronze", "Processos", zzq["oculto"], "numero") is None
    assert sv.valor_original(db, S, "bronze", "Andamentos", zzq["a_oculto"], "descricao") is None


def test_b2_pendencias_sem_linha_reclassificada(db, zzq):
    p = sv.pendencias(db, S, 1, {})
    codigos = [x["motivo_codigo"] for x in p["linhas"]]
    assert codigos == ["quarentena:chave_nula"]
    assert all(sv.QUAR_OCULTA not in c for c in codigos)
    assert [x["tabela"] for x in p["linhas"]] == ["Andamentos"]
    f = sv.pendencias(db, S, 1, {"motivo": "em_quarentena"})
    assert len(f["linhas"]) == 1


def test_b2_amostra_sem_linha_reclassificada(db, zzq):
    a = sv.amostra(db, S, "bronze", "Processos", 1, {})
    assert [x["id_origem"] for x in a["linhas"]] == ["ZZTESTE-1", "ZZTESTE-2"]
    assert OCULTO_VAL not in json.dumps(a, default=str)
