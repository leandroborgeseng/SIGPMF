"""Bases retiradas do catálogo, do mapeamento e da agenda.

Nada é apagado: o seed continua ON CONFLICT DO NOTHING e a API só deixa de listar.
"""
from sqlalchemy import text

from app.catalogo.bases import BASES, RETIRADAS
from app.connectors.sqlserver.politicas import BASES_DESCARTE, BASES_PERMITIDAS
from app.ingestion.mapeamentos import MAPEAMENTOS
from app.services.carga_conector import CONECTORES


def test_retiradas_saem_do_catalogo_mapeamento_agenda_e_da_tela(client, tokens, db):
    nomes = {b[0] for b in BASES}
    assert RETIRADAS == {
        "Geral", "IPTU_Isencao", "AtendSaude", "GED", "Iptu_Anteriores", "Divida_Anteriores",
        "Financeiro_Anteriores", "Estoque_Anterior", "Baixas_Ant", "LogSistemas",
        "LogSistemas_Anteriores", "LogSistemas_Ate2014",
    }
    assert RETIRADAS.isdisjoint(nomes)
    assert RETIRADAS <= BASES_DESCARTE
    assert RETIRADAS.isdisjoint(BASES_PERMITIDAS)
    assert RETIRADAS.isdisjoint({sistema for sistema, _tabela in MAPEAMENTOS})
    assert RETIRADAS.isdisjoint({banco for _sistema, banco in CONECTORES.values()})
    conectores = {row[0] for row in db.execute(text("select conector from controle.agendamentos"))}
    assert RETIRADAS.isdisjoint(conectores)
    # linha legada permanece no banco e some da resposta
    db.execute(text("""
        INSERT INTO controle.bases_catalogo (nome, grupo, triagem, status, prioridade, sistemas, conjunto, descricao)
        VALUES ('Geral', 'descartavel', 'schema', 'excluido', NULL, '[]'::jsonb, NULL, 'linha legada ficticia')
        ON CONFLICT (nome) DO NOTHING
    """))
    db.commit()
    r = client.get("/api/conectores", headers=tokens["leitura"])
    assert r.status_code == 200
    assert "Geral" not in {b["nome"] for b in r.json()["bases"]}
    assert RETIRADAS.isdisjoint({b["nome"] for b in r.json()["bases"]})
