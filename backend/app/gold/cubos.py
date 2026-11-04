"""Cubos do ouro (CB1): agregados com várias dimensões, base dos cruzamentos e dos painéis com filtros. Sem pessoa na API.

- Cada cubo é reconstruído por CREATE TABLE AS (o Postgres usa consulta paralela; INSERT ... SELECT não usa) numa tabela
  nova, trocada pela antiga numa transação curta: quem está lendo não vê tabela pela metade, e não sobra lixo para o vacuum.
- Fora do caminho das cargas: roda pela agenda (item `cubos`) ou pelo botão Recalcular. Erro num cubo não derruba carga,
  e os cubos seguintes ainda rodam.
- Bairro: nome normalizado (gold.norm_bairro: sem acento, JD -> JARDIM...), com id 0 = "Não identificado". A dimensão junta
  o cadastro de bairros do Geral com os textos das outras bases. O id vem do hash do nome normalizado: é estável entre
  recálculos, então um cubo antigo continua apontando para o bairro certo enquanto os outros são trocados.
- gold.cubo_aux_pessoa é auxiliar (cidadão -> bairro e faixa) e não é exposta pela API.
"""
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.controle import Execucao
from app.silver.fatos import NOMES_TRIBUTO, nomes

log = logging.getLogger("sigpmf.cubos")
TIPO = "cubos"
SISTEMA = "CUBOS"
USUARIO_AGENDA = "agenda"
ORFA_MIN = 60

_DIM_BAIRRO = """
    WITH f AS (
        SELECT gold.norm_bairro(bairro) AS norm, bairro AS nome FROM ref.geral_bairro
        UNION ALL SELECT gold.norm_bairro(bairro), bairro FROM silver.enderecos WHERE mais_recente
        UNION ALL SELECT gold.norm_bairro(bairro), bairro FROM silver.escola_alunos
        UNION ALL SELECT gold.norm_bairro(bairro), bairro FROM silver.empresas_sil)
    SELECT (hashtextextended(norm, 0) & 9007199254740991) + 1 AS id, norm, initcap(lower(mode() WITHIN GROUP (ORDER BY nome))) AS nome
    FROM f WHERE norm IS NOT NULL GROUP BY norm
    UNION ALL SELECT CAST(0 AS bigint), '', 'Não identificado'
"""
_FAIXA_SALDO = """CASE WHEN d.saldo < 1000 THEN 'até R$ 1 mil' WHEN d.saldo < 10000 THEN 'R$ 1 a 10 mil'
    WHEN d.saldo < 100000 THEN 'R$ 10 a 100 mil' ELSE 'acima de R$ 100 mil' END"""
_FAIXA_RH = """CASE WHEN vencimentos < 3000 THEN 'até R$ 3 mil' WHEN vencimentos < 5000 THEN 'R$ 3 a 5 mil'
    WHEN vencimentos < 8000 THEN 'R$ 5 a 8 mil' WHEN vencimentos < 12000 THEN 'R$ 8 a 12 mil'
    WHEN vencimentos < 20000 THEN 'R$ 12 a 20 mil' ELSE 'acima de R$ 20 mil' END"""

# Mesmo nome de tributo dos painéis, para o clique num gráfico chegar ao mesmo recorte
_TRIBUTO = "CASE COALESCE(l.tributo, '') " + " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in NOMES_TRIBUTO.items()) + " WHEN '' THEN 'Outro' ELSE l.tributo END"

# (tabela, SELECT, colunas a indexar). A ordem importa: dimensões e o auxiliar antes dos cubos.
PASSOS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("dim_bairro", _DIM_BAIRRO, ("id", "norm")),
    ("cubo_aux_codigo_bairro", """
        SELECT g.cd_bai, b.id AS bairro_id FROM ref.geral_bairro g JOIN gold.dim_bairro b ON b.norm = gold.norm_bairro(g.bairro)
    """, ("cd_bai",)),
    ("cubo_aux_pessoa", """
        SELECT c.cidadao_id, COALESCE(b.id, 0) AS bairro_id, gold.faixa_etaria(c.data_nascimento) AS faixa, COALESCE(c.falecido, false) AS falecido,
               t.cidadao_id IS NOT NULL AS consultou_12m, v.cidadao_id IS NOT NULL AS vacinou_24m
        FROM silver.cidadaos c
        LEFT JOIN (SELECT DISTINCT ON (cidadao_id) cidadao_id, gold.norm_bairro(bairro) AS norm FROM silver.enderecos
                   WHERE mais_recente AND bairro IS NOT NULL ORDER BY cidadao_id, fonte_atualizado_em DESC NULLS LAST) e USING (cidadao_id)
        LEFT JOIN gold.dim_bairro b ON b.norm = e.norm
        LEFT JOIN (SELECT DISTINCT cidadao_id FROM silver.atendimentos WHERE data >= current_date - interval '12 months') t ON t.cidadao_id = c.cidadao_id
        LEFT JOIN (SELECT DISTINCT cidadao_id FROM silver.vacinacoes WHERE data >= current_date - interval '24 months') v ON v.cidadao_id = c.cidadao_id
    """, ("cidadao_id",)),
    ("cubo_pessoas", """
        SELECT bairro_id, faixa, falecido, consultou_12m, vacinou_24m, count(*) AS pessoas FROM gold.cubo_aux_pessoa GROUP BY 1, 2, 3, 4, 5
    """, ("bairro_id",)),
    ("cubo_saude", """
        WITH e AS (
            SELECT 'consulta' AS tipo, data AS d, cidadao_id, unidade, especialidade, CAST(NULL AS integer) AS espera, CAST(NULL AS text) AS vacina FROM silver.atendimentos WHERE data IS NOT NULL
            UNION ALL SELECT 'agendamento', data_consulta, cidadao_id, unidade, especialidade, dias_espera, NULL FROM silver.agendamentos WHERE data_consulta IS NOT NULL
            UNION ALL SELECT 'vacina', data, cidadao_id, unidade, NULL, NULL, COALESCE(vacina, 'Não informada') FROM silver.vacinacoes WHERE data IS NOT NULL)
        SELECT CAST(extract(year FROM e.d) AS integer) AS ano, CAST(extract(month FROM e.d) AS integer) AS mes, e.tipo,
               COALESCE(p.bairro_id, 0) AS bairro_id, COALESCE(p.faixa, 'não informada') AS faixa, COALESCE(cu.bairro_id, 0) AS unidade_bairro_id,
               COALESCE(u.nome, 'Unidade ' || COALESCE(CAST(e.unidade AS text), '?')) AS unidade,
               COALESCE(x.nome, CASE WHEN e.especialidade IS NULL THEN 'Sem especialidade informada' ELSE 'Código ' || e.especialidade || ' (sem nome no cadastro)' END) AS especialidade,
               COALESCE(e.vacina, 'Não se aplica') AS vacina,
               count(*) AS eventos, COALESCE(sum(e.espera), 0) AS espera_soma, count(e.espera) AS espera_n
        FROM e LEFT JOIN gold.cubo_aux_pessoa p USING (cidadao_id)
        LEFT JOIN silver.unidades_saude u ON u.codigo = e.unidade
        LEFT JOIN gold.cubo_aux_codigo_bairro cu ON cu.cd_bai = u.cd_bairro
        LEFT JOIN gold.dim_especialidade x ON x.codigo = e.especialidade
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
    """, ("ano", "bairro_id", "tipo")),
    ("cubo_imoveis", """
        SELECT COALESCE(c.bairro_id, 0) AS bairro_id, COALESCE(i.situacao, 'não informada') AS situacao, count(*) AS imoveis,
               COALESCE(sum(i.iptu_anual), 0) AS iptu_anual, COALESCE(sum(i.area_terreno), 0) AS area_terreno,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM silver.imovel_possuidores p WHERE p.codi = i.codi AND p.ativo)) AS com_possuidor
        FROM silver.imoveis i LEFT JOIN gold.cubo_aux_codigo_bairro c ON c.cd_bai = i.cd_bairro GROUP BY 1, 2
    """, ("bairro_id",)),
    ("cubo_tributario", """
        SELECT l.exercicio, CAST(extract(year FROM l.vencimento) AS integer) AS ano_venc, CAST(extract(month FROM l.vencimento) AS integer) AS mes,
               {TRIBUTO} AS tributo, COALESCE(c.bairro_id, 0) AS bairro_id,
               count(*) AS parcelas, COALESCE(sum(l.valor), 0) AS lancado, COALESCE(sum(l.valor_pago), 0) AS pago,
               COALESCE(sum(l.valor) FILTER (WHERE l.vencimento < current_date), 0) AS vencido,
               COALESCE(sum(l.valor) FILTER (WHERE l.vencimento < current_date AND NOT l.pago), 0) AS vencido_aberto
        FROM silver.lancamentos l LEFT JOIN silver.imoveis i ON i.codi = l.codi LEFT JOIN gold.cubo_aux_codigo_bairro c ON c.cd_bai = i.cd_bairro
        GROUP BY 1, 2, 3, 4, 5
    """.replace("{TRIBUTO}", _TRIBUTO), ("exercicio", "bairro_id")),
    ("cubo_divida", f"""
        SELECT d.ano, COALESCE(n.descricao, 'Natureza ' || d.natureza, 'Não informada') AS natureza, COALESCE(c.bairro_id, 0) AS bairro_id, {_FAIXA_SALDO} AS faixa_saldo,
               COALESCE(d.protestos, 0) > 0 AS protestada, COALESCE(d.processo, '') <> '' AS em_processo,
               count(*) AS inscricoes, COALESCE(sum(d.saldo), 0) AS saldo
        FROM silver.dividas d LEFT JOIN silver.imoveis i ON i.codi = d.codi LEFT JOIN gold.cubo_aux_codigo_bairro c ON c.cd_bai = i.cd_bairro
        LEFT JOIN ref.divida_natureza n ON n.codigo = d.natureza
        WHERE d.extinta IS NOT TRUE AND COALESCE(d.saldo, 0) > 0 GROUP BY 1, 2, 3, 4, 5, 6
    """, ("bairro_id",)),
    ("cubo_empresas", """
        SELECT COALESCE(b.id, 0) AS bairro_id, COALESCE(e.tipo_pessoa, '?') AS tipo_pessoa, COALESCE(e.possui_debito, false) AS possui_debito, count(*) AS empresas
        FROM silver.empresas_sil e LEFT JOIN gold.dim_bairro b ON b.norm = gold.norm_bairro(e.bairro) GROUP BY 1, 2, 3
    """, ("bairro_id",)),
    ("cubo_educacao", """
        SELECT COALESCE(b.id, p.bairro_id, 0) AS bairro_id, gold.faixa_etaria(a.data_nascimento) AS faixa, COALESCE(a.situacao = 1, false) AS ativo,
               COALESCE(m.escola, 'Sem matrícula atual') AS escola, count(*) AS alunos
        FROM silver.escola_alunos a
        LEFT JOIN gold.dim_bairro b ON b.norm = gold.norm_bairro(a.bairro)
        LEFT JOIN gold.cubo_aux_pessoa p ON p.cidadao_id = a.cidadao_id
        LEFT JOIN (SELECT DISTINCT ON (alu_id) alu_id, escola FROM silver.escola_matriculas WHERE atual AND excluida IS NOT TRUE ORDER BY alu_id, id DESC) m ON m.alu_id = a.alu_id
        WHERE a.excluido IS NOT TRUE GROUP BY 1, 2, 3, 4
    """, ("bairro_id",)),
    ("cubo_rh", f"""
        SELECT ano, mes, secretaria, grupo, COALESCE(cargo, 'Não informado') AS cargo, {_FAIXA_RH} AS faixa_salario,
               count(*) AS pessoas, sum(vencimentos) AS bruto, sum(liquido) AS liquido
        FROM silver.rh_remuneracao WHERE vencimentos > 0 GROUP BY 1, 2, 3, 4, 5, 6
    """, ("ano", "secretaria")),
    ("cubo_despesa", """
        SELECT ano, mes, COALESCE(orgao, '?') AS orgao, COALESCE(funcao, '?') AS funcao, COALESCE(natureza, 'outra') AS natureza, fase,
               COALESCE(modalidade_licitacao, '?') AS modalidade, COALESCE(fonte_recurso, '?') AS fonte, sum(valor) AS valor
        FROM silver.despesa GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
    """, ("ano", "funcao")),
    ("cubo_receita", """
        SELECT ano, mes, COALESCE(orgao, '?') AS orgao, COALESCE(subcategoria, '?') AS subcategoria, COALESCE(d2, '?') AS item, sum(valor) AS valor
        FROM silver.receita GROUP BY 1, 2, 3, 4, 5
    """, ("ano",)),
)
TABELAS = tuple(p[0] for p in PASSOS)


def _dim_especialidade(db: Session) -> None:
    """Os nomes das especialidades vêm da bronze (mesma leitura dos painéis)."""
    esp = [{"c": k, "n": v} for k, v in nomes(db)["especialidades"].items() if k is not None and v]
    db.execute(text("DROP TABLE IF EXISTS gold.dim_especialidade"))
    db.execute(text("CREATE TABLE gold.dim_especialidade (codigo integer PRIMARY KEY, nome text NOT NULL)"))
    if esp:
        db.execute(text("INSERT INTO gold.dim_especialidade (codigo, nome) VALUES (:c, :n)"), esp)
    db.commit()


def _troca(db: Session, tabela: str, sql: str, indices: tuple[str, ...]) -> int:
    t0 = time.monotonic()
    novo = f"gold.{tabela}__novo"
    db.execute(text(f"DROP TABLE IF EXISTS {novo}"))
    db.execute(text(f"CREATE TABLE {novo} AS {sql}"))
    for col in indices:
        db.execute(text(f"CREATE INDEX ON {novo} ({col})"))
    db.execute(text(f"ANALYZE {novo}"))
    linhas = db.scalar(text(f"SELECT count(*) FROM {novo}"))
    db.commit()  # até aqui nada tranca a tabela em uso; a troca abaixo é instantânea
    db.execute(text(f"DROP TABLE IF EXISTS gold.{tabela}"))
    db.execute(text(f"ALTER TABLE {novo} RENAME TO {tabela}"))
    db.execute(text("""INSERT INTO gold.cubos_estado (cubo, linhas, segundos, gerado_em) VALUES (:c, :l, :s, now())
                       ON CONFLICT (cubo) DO UPDATE SET linhas = EXCLUDED.linhas, segundos = EXCLUDED.segundos, gerado_em = now()"""),
               {"c": tabela, "l": linhas, "s": round(time.monotonic() - t0, 2)})
    db.commit()
    return linhas


def reconstruir(db: Session, progresso: Callable[[str], None] | None = None) -> dict[str, int]:
    """Recalcula tudo, um cubo por vez. Devolve linhas por cubo; levanta só depois de tentar todos."""
    _dim_especialidade(db)
    out: dict[str, int] = {}
    falhas: list[str] = []
    for tabela, sql, indices in PASSOS:
        if progresso:
            progresso(tabela)
        try:
            out[tabela] = _troca(db, tabela, sql, indices)
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("Cubo %s não recalculado", tabela)
            falhas.append(tabela)
    if falhas:
        raise RuntimeError(f"Cubos não recalculados: {', '.join(falhas)}.")
    return out


def _fim(db: Session, ex: Execucao, status: str, mensagem: str) -> Execucao:
    ex.status, ex.mensagem, ex.etapa, ex.tabela_atual = status, mensagem, "concluida", None
    ex.finalizado_em = ex.atualizado_em = datetime.now(UTC)
    db.commit()
    return ex


class CalculoRecusado(RuntimeError):
    """Já existe recálculo em andamento."""


def em_andamento(db: Session) -> Execucao | None:
    ex = db.scalar(select(Execucao).where(Execucao.tipo == TIPO, Execucao.status == "em_andamento").order_by(Execucao.id.desc()).limit(1))
    if ex is not None and (ex.atualizado_em or ex.iniciado_em) < datetime.now(UTC) - timedelta(minutes=ORFA_MIN):
        _fim(db, ex, "interrompida", "Recálculo interrompido (o servidor parou). Recalcule de novo.")
        return None
    return ex


def _nova(db: Session, usuario: str | None) -> Execucao:
    if em_andamento(db) is not None:
        raise CalculoRecusado("Já existe um recálculo dos cubos em andamento.")
    ex = Execucao(tipo=TIPO, sistema=SISTEMA, status="em_andamento", usuario=usuario, modo="completa", etapa="ouro", atualizado_em=datetime.now(UTC))
    db.add(ex)
    db.commit()
    return ex


def executar(db: Session, usuario: str | None = USUARIO_AGENDA, ex: Execucao | None = None) -> Execucao:
    ex = ex or _nova(db, usuario)

    def passo(tabela: str) -> None:
        ex.tabela_atual, ex.atualizado_em = tabela, datetime.now(UTC)
        db.commit()
    t0 = time.monotonic()
    try:
        linhas = reconstruir(db, passo)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return _fim(db, ex, "falha", str(exc)[:400] if isinstance(exc, RuntimeError) else f"Recálculo não concluído ({type(exc).__name__}).")
    ex.lidos = sum(linhas.values())
    return _fim(db, ex, "sucesso", f"{len(linhas)} tabelas recalculadas em {time.monotonic() - t0:.0f} s.")


def iniciar_em_fundo(db: Session, usuario: str) -> Execucao:
    ex = _nova(db, usuario)
    disparar(ex.id)
    return ex


def disparar(ex_id: int) -> None:
    """Background (thread daemon). Os testes trocam por execução síncrona."""
    threading.Thread(target=rodar, args=(ex_id,), daemon=True, name=f"cubos-{ex_id}").start()


def rodar(ex_id: int) -> None:
    from app.core.db import SessionLocal
    db = SessionLocal()
    try:
        ex = db.get(Execucao, ex_id)
        if ex is not None and ex.status == "em_andamento":
            executar(db, ex.usuario, ex=ex)
    except Exception:  # noqa: BLE001
        log.exception("Cubos: execução %s caiu fora do laço", ex_id)
    finally:
        db.close()


def estado(db: Session) -> dict:
    def ex_dict(e: Execucao | None) -> dict | None:
        return None if e is None else {"status": e.status, "mensagem": e.mensagem, "passo": e.tabela_atual, "iniciado_em": e.iniciado_em, "finalizado_em": e.finalizado_em}
    tabelas = [{"cubo": c, "linhas": n, "segundos": float(s), "gerado_em": g} for c, n, s, g in db.execute(
        text("SELECT cubo, linhas, segundos, gerado_em FROM gold.cubos_estado ORDER BY cubo"))]
    ult = db.scalar(select(Execucao).where(Execucao.tipo == TIPO).order_by(Execucao.id.desc()).limit(1))
    return {"tabelas": tabelas, "em_andamento": ex_dict(em_andamento(db)), "ultima_execucao": ex_dict(ult)}


def geracao(db: Session) -> str | None:
    """Carimbo do último recálculo: a chave do cache em memória dos cruzamentos e das consultas."""
    g = db.scalar(text("SELECT max(gerado_em) FROM gold.cubos_estado"))
    return g.isoformat() if g else None
