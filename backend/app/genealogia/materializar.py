"""Pré-calcula gold.genealogia_arestas. Idempotente: upsert pela chave natural e geração nova.

A leitura da ficha usa só a geração gravada em gold.genealogia_estado. Linha que deixou de
existir permanece com a geração anterior e some da leitura, sem apagar registro.

Diferenças em relação ao casamento em Python (app.genealogia.confianca / provedores._casa):

- Nome idêntico (0,70), Jaccard de tokens (0,50 se >= 0,75), bônus de idade 14–60 anos (+0,15),
  bônus de CEP de 8 dígitos ou logradouro normalizado (+0,15), piso 0,50 e teto 0,99 são os mesmos.
- Homônimos de nome idêntico só entram com um único candidato com bônus. Sem esse único bônus,
  não há aresta. A aresta forte de geração (qualquer sentido do par) impede a fraca.
- A faixa 0,55 usa pg_trgm similarity >= 0,90 quando a extensão existe. Não é o difflib.SequenceMatcher.
  Sem permissão para CREATE EXTENSION pg_trgm, essa faixa não é emitida: ficam o nome idêntico e o Jaccard.
- O bloqueio por primeiro token limita a 200 candidatos, na ordem de cidadao_id (o Python usava a ordem
  de inserção, instável). A busca não compara a cidade inteira com ela mesma.
- As duas pontas precisam ser silver.cidadaos. Nome de mãe que não é cidadão não vira nó (igual ao Python).
  Filho com CPF válido que ainda não está na prata também não gera aresta (o Python criava um nó solto).
- Dois cidadao_id distintos com o mesmo CPF não são fundidos na gravação. A leitura funde pelo CPF.

Roda no fim de app.gold.visao.reconstruir e sozinho por `python -m scripts.genealogia_arestas`.
"""
import logging
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_EXATA = """
WITH textos AS (
  /*TEXTOS*/
),
cand AS (
  SELECT t.origem, p.cidadao_id AS destino, t.parentesco, t.base, t.fonte,
    (t.nasc IS NOT NULL AND p.nascimento IS NOT NULL AND t.nasc > p.nascimento
      AND (t.nasc - p.nascimento)::numeric BETWEEN (14 * 365.25) AND (60 * 365.25)) AS idade,
    ((t.cep IS NOT NULL AND p.cep IS NOT NULL AND t.cep = p.cep)
      OR (COALESCE(t.log_norm, '') <> '' AND COALESCE(p.logradouro_norm, '') <> ''
          AND t.log_norm = p.logradouro_norm)) AS endereco
  FROM textos t
  JOIN gold.genealogia_pessoas p
    ON p.geracao = :g AND p.nome_norm = t.texto_norm AND p.cidadao_id <> t.origem
  WHERE COALESCE(t.texto_norm, '') <> ''
),
marc AS (
  SELECT *,
    count(*) OVER (PARTITION BY origem, base, parentesco) AS n,
    count(*) FILTER (WHERE idade OR endereco) OVER (PARTITION BY origem, base, parentesco) AS n_bonus
  FROM cand
),
escolha AS (
  SELECT * FROM marc
  WHERE n = 1 OR ((idade OR endereco) AND n_bonus = 1)
)
INSERT INTO gold.genealogia_arestas (
  origem, destino, sentido, parentesco, forca, confianca, base, criterio, fonte_atualizado_em, ativo, geracao
)
SELECT DISTINCT ON (origem, destino, base, parentesco)
  origem, destino, 'ascendente', parentesco, 'fraco',
  LEAST(0.99::numeric, 0.70
    + CASE WHEN idade THEN 0.15 ELSE 0 END
    + CASE WHEN endereco THEN 0.15 ELSE 0 END),
  base,
  'nome normalizado idêntico'
    || CASE WHEN idade THEN ' + diferença de idade compatível' ELSE '' END
    || CASE WHEN endereco THEN ' + CEP ou logradouro coincidente' ELSE '' END,
  fonte, true, :g
FROM escolha e
WHERE NOT EXISTS (
  SELECT 1 FROM gold.genealogia_arestas s
   WHERE s.geracao = :g AND s.forca = 'forte' AND s.sentido IN ('ascendente', 'descendente')
     AND ((s.origem = e.origem AND s.destino = e.destino)
       OR (s.origem = e.destino AND s.destino = e.origem))
)
ORDER BY origem, destino, base, parentesco,
  LEAST(0.99::numeric, 0.70
    + CASE WHEN idade THEN 0.15 ELSE 0 END
    + CASE WHEN endereco THEN 0.15 ELSE 0 END) DESC
ON CONFLICT ON CONSTRAINT uq_gold_genealogia_aresta DO UPDATE SET
  confianca = EXCLUDED.confianca,
  criterio = EXCLUDED.criterio,
  fonte_atualizado_em = EXCLUDED.fonte_atualizado_em,
  ativo = true,
  geracao = EXCLUDED.geracao
"""

_SIMILAR = """
WITH textos AS (
  /*TEXTOS*/
),
sem_exato AS (
  SELECT t.*
    FROM textos t
   WHERE COALESCE(t.texto_norm, '') <> ''
     AND NOT EXISTS (
       SELECT 1 FROM gold.genealogia_pessoas p
        WHERE p.geracao = :g AND p.nome_norm = t.texto_norm AND p.cidadao_id <> t.origem)
),
lim AS (
  SELECT s.origem, s.parentesco, s.base, s.fonte, s.nasc, s.cep, s.log_norm, s.texto_norm,
         c.cidadao_id AS destino, c.nome_norm, c.nascimento, c.cep AS cep_c, c.logradouro_norm
    FROM sem_exato s
    JOIN LATERAL (
      SELECT p.cidadao_id, p.nome_norm, p.nascimento, p.cep, p.logradouro_norm
        FROM gold.genealogia_pessoas p
       WHERE p.geracao = :g
         AND p.primeiro_token = split_part(s.texto_norm, ' ', 1)
         AND p.primeiro_token <> ''
         AND p.cidadao_id <> s.origem
         AND p.nome_norm <> s.texto_norm
       ORDER BY p.cidadao_id
       LIMIT 200
    ) c ON true
),
pontos AS (
  SELECT *, gold.jaccard_tokens(texto_norm, nome_norm) AS jac, /*SIM*/ AS sim
    FROM lim
),
base AS (
  SELECT *,
    CASE WHEN sim >= 0.90 THEN 0.55 WHEN jac >= 0.75 THEN 0.50 ELSE NULL END AS nota
    FROM pontos
),
com AS (
  SELECT *,
    (nasc IS NOT NULL AND nascimento IS NOT NULL AND nasc > nascimento
      AND (nasc - nascimento)::numeric BETWEEN (14 * 365.25) AND (60 * 365.25)) AS idade,
    ((cep IS NOT NULL AND cep_c IS NOT NULL AND cep = cep_c)
      OR (COALESCE(log_norm, '') <> '' AND COALESCE(logradouro_norm, '') <> ''
          AND log_norm = logradouro_norm)) AS endereco,
    LEAST(0.99::numeric, nota
      + CASE WHEN (nasc IS NOT NULL AND nascimento IS NOT NULL AND nasc > nascimento
                   AND (nasc - nascimento)::numeric BETWEEN (14 * 365.25) AND (60 * 365.25))
             THEN 0.15 ELSE 0 END
      + CASE WHEN ((cep IS NOT NULL AND cep_c IS NOT NULL AND cep = cep_c)
                   OR (COALESCE(log_norm, '') <> '' AND COALESCE(logradouro_norm, '') <> ''
                       AND log_norm = logradouro_norm))
             THEN 0.15 ELSE 0 END) AS confianca
    FROM base
   WHERE nota IS NOT NULL
),
topo AS (
  SELECT *,
    max(confianca) OVER (PARTITION BY origem, base, parentesco) AS topo,
    count(*) OVER (PARTITION BY origem, base, parentesco, confianca) AS n_mesma
    FROM com
)
INSERT INTO gold.genealogia_arestas (
  origem, destino, sentido, parentesco, forca, confianca, base, criterio, fonte_atualizado_em, ativo, geracao
)
SELECT DISTINCT ON (origem, destino, base, parentesco)
  origem, destino, 'ascendente', parentesco, 'fraco', confianca, base,
  CASE WHEN sim >= 0.90 THEN 'similaridade pg_trgm ≥ 0,90'
       ELSE 'Jaccard de tokens ≥ 0,75' END
    || CASE WHEN idade THEN ' + diferença de idade compatível' ELSE '' END
    || CASE WHEN endereco THEN ' + CEP ou logradouro coincidente' ELSE '' END,
  fonte, true, :g
FROM topo e
WHERE confianca = topo AND n_mesma = 1
  AND NOT EXISTS (
    SELECT 1 FROM gold.genealogia_arestas s
     WHERE s.geracao = :g AND s.forca = 'forte' AND s.sentido IN ('ascendente', 'descendente')
       AND ((s.origem = e.origem AND s.destino = e.destino)
         OR (s.origem = e.destino AND s.destino = e.origem))
  )
ORDER BY origem, destino, base, parentesco, confianca DESC
ON CONFLICT ON CONSTRAINT uq_gold_genealogia_aresta DO UPDATE SET
  confianca = EXCLUDED.confianca,
  criterio = EXCLUDED.criterio,
  fonte_atualizado_em = EXCLUDED.fonte_atualizado_em,
  ativo = true,
  geracao = EXCLUDED.geracao
"""


def _campo(nome: str) -> str:
    if not nome:
        return "NULL::text"
    return "u.dados->>'" + nome.replace("'", "") + "'"


def _textos_bronze(nome: str, cpf: str, nasc: str, mae: str, pai: str, cep: str, logradouro: str) -> str:
    cpf_e, nome_e, nasc_e = _campo(cpf), _campo(nome), _campo(nasc)
    mae_e, pai_e, cep_e, log_e = _campo(mae), _campo(pai), _campo(cep), _campo(logradouro)
    return f"""
    WITH ult AS (
      SELECT DISTINCT ON (id_origem) id_origem, dados, atualizado_origem_em
        FROM bronze.registros
       WHERE sistema_origem = :sistema AND tabela = :tabela AND quarentena IS NULL
       ORDER BY id_origem, id DESC
    ),
    base AS (
      SELECT
        CASE
          WHEN gold.cpf_valido_digitos({cpf_e}) IS NOT NULL THEN ic.cidadao_id
          WHEN v.cidadao_id IS NOT NULL THEN v.cidadao_id
          WHEN nu.n = 1 THEN nu.cid
        END AS origem,
        gold.nome_norm({mae_e}) AS mae,
        gold.nome_norm({pai_e}) AS pai,
        COALESCE(gold.data_iso({nasc_e}), pe.nascimento) AS nasc,
        COALESCE(gold.cep8({cep_e}), pe.cep) AS cep,
        COALESCE(NULLIF(gold.nome_norm({log_e}), ''), pe.logradouro_norm) AS log_norm,
        u.atualizado_origem_em AS fonte
      FROM ult u
      LEFT JOIN silver.identificadores ic
        ON ic.tipo = 'cpf' AND ic.valor = gold.cpf_valido_digitos({cpf_e})
      LEFT JOIN silver.vinculos v
        ON gold.cpf_valido_digitos({cpf_e}) IS NULL
       AND v.sistema = :sistema AND v.tabela = :tabela AND v.chave_origem = u.id_origem
      LEFT JOIN (
        SELECT nome_norm, min(cidadao_id) AS cid, count(*)::int AS n
          FROM gold.genealogia_pessoas
         WHERE geracao = :g AND nome_norm <> ''
         GROUP BY nome_norm
      ) nu
        ON gold.cpf_valido_digitos({cpf_e}) IS NULL
       AND v.cidadao_id IS NULL
       AND nu.nome_norm = gold.nome_norm({nome_e})
      LEFT JOIN gold.genealogia_pessoas pe
        ON pe.geracao = :g
       AND pe.cidadao_id = CASE
            WHEN gold.cpf_valido_digitos({cpf_e}) IS NOT NULL THEN ic.cidadao_id
            WHEN v.cidadao_id IS NOT NULL THEN v.cidadao_id
            WHEN nu.n = 1 THEN nu.cid
          END
    )
    SELECT origem, mae AS texto_norm, 'mãe'::text AS parentesco, :base AS base,
           nasc, cep, log_norm, fonte
      FROM base WHERE origem IS NOT NULL AND COALESCE(mae, '') <> ''
    UNION ALL
    SELECT origem, pai, 'pai'::text, :base, nasc, cep, log_norm, fonte
      FROM base WHERE origem IS NOT NULL AND COALESCE(pai, '') <> ''
    """


_MUNICIPES = """
SELECT COALESCE(m.cidadao_id, i.cidadao_id) AS origem,
       gold.nome_norm(m.nome_mae) AS texto_norm,
       'mãe'::text AS parentesco,
       'ISS'::text AS base,
       COALESCE(m.data_nascimento, pe.nascimento) AS nasc,
       pe.cep AS cep,
       pe.logradouro_norm AS log_norm,
       m.data_alteracao::timestamptz AS fonte
  FROM silver.municipes m
  LEFT JOIN silver.identificadores i
    ON m.cidadao_id IS NULL AND i.tipo = 'cpf' AND i.valor = gold.cpf_valido_digitos(m.documento)
  LEFT JOIN gold.genealogia_pessoas pe
    ON pe.geracao = :g AND pe.cidadao_id = COALESCE(m.cidadao_id, i.cidadao_id)
 WHERE m.tipo = 'cidadao'
   AND COALESCE(gold.nome_norm(m.nome_mae), '') <> ''
   AND COALESCE(m.cidadao_id, i.cidadao_id) IS NOT NULL
"""


def _tem_trgm(db: Session) -> bool:
    db.execute(text("SAVEPOINT sigpmf_trgm"))
    try:
        db.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except Exception:
        db.execute(text("ROLLBACK TO SAVEPOINT sigpmf_trgm"))
        return False
    db.execute(text("RELEASE SAVEPOINT sigpmf_trgm"))
    db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_gold_genealogia_pessoas_nome_trgm "
        "ON gold.genealogia_pessoas USING gin (nome_norm gin_trgm_ops)"))
    return True


def _geracao(db: Session) -> int:
    return int(db.scalar(text("""
        SELECT GREATEST(
          COALESCE((SELECT geracao FROM gold.genealogia_estado WHERE id = 1), 0),
          COALESCE((SELECT max(geracao) FROM gold.genealogia_arestas), 0)
        ) + 1
    """)))


def _pessoas(db: Session, geracao: int) -> None:
    db.execute(text("""
        INSERT INTO gold.genealogia_pessoas (
          cidadao_id, nome, nome_norm, primeiro_token, cpf, nascimento, cep, logradouro_norm, ativo, geracao)
        SELECT c.cidadao_id, c.nome, gold.nome_norm(c.nome),
               NULLIF(split_part(gold.nome_norm(c.nome), ' ', 1), ''),
               cpf.valor, c.data_nascimento, e.cep, NULLIF(e.logradouro_norm, ''), true, :g
          FROM silver.cidadaos c
          LEFT JOIN (
            SELECT DISTINCT ON (cidadao_id) cidadao_id, valor
              FROM silver.identificadores WHERE tipo = 'cpf'
             ORDER BY cidadao_id, id
          ) cpf ON cpf.cidadao_id = c.cidadao_id
          LEFT JOIN (
            SELECT DISTINCT ON (cidadao_id) cidadao_id, gold.cep8(cep) AS cep,
                   gold.nome_norm(logradouro) AS logradouro_norm
              FROM silver.enderecos
             ORDER BY cidadao_id, mais_recente DESC, id DESC
          ) e ON e.cidadao_id = c.cidadao_id
        ON CONFLICT (cidadao_id) DO UPDATE SET
          nome = EXCLUDED.nome,
          nome_norm = EXCLUDED.nome_norm,
          primeiro_token = EXCLUDED.primeiro_token,
          cpf = EXCLUDED.cpf,
          nascimento = EXCLUDED.nascimento,
          cep = EXCLUDED.cep,
          logradouro_norm = EXCLUDED.logradouro_norm,
          ativo = true,
          geracao = EXCLUDED.geracao
    """), {"g": geracao})


def _fortes(db: Session, geracao: int) -> None:
    db.execute(text("""
        INSERT INTO gold.genealogia_arestas (
          origem, destino, sentido, parentesco, forca, confianca, base, criterio, fonte_atualizado_em, ativo, geracao)
        SELECT d.cidadao_titular_id, d.cidadao_id,
               CASE WHEN d.papel IN ('ascendente', 'descendente', 'conjuge', 'outro') THEN d.papel ELSE 'outro' END,
               left(COALESCE(NULLIF(btrim(d.grau), ''), 'parentesco'), 80),
               'forte', 1.0, 'SENIOR',
               'CPF do titular e do dependente na r036dep (numcad + coddep)',
               d.fonte_atualizado_em, true, :g
          FROM silver.senior_dependentes d
         WHERE d.cidadao_id IS NOT NULL AND d.cidadao_titular_id IS NOT NULL
           AND d.cidadao_id <> d.cidadao_titular_id
        ON CONFLICT ON CONSTRAINT uq_gold_genealogia_aresta DO UPDATE SET
          confianca = EXCLUDED.confianca, criterio = EXCLUDED.criterio,
          fonte_atualizado_em = EXCLUDED.fonte_atualizado_em, ativo = true, geracao = EXCLUDED.geracao
    """), {"g": geracao})
    db.execute(text("""
        INSERT INTO gold.genealogia_arestas (
          origem, destino, sentido, parentesco, forca, confianca, base, criterio, fonte_atualizado_em, ativo, geracao)
        SELECT DISTINCT ON (filho.cidadao_id, pai.cidadao_id, parentesco)
               filho.cidadao_id, pai.cidadao_id, 'ascendente', parentesco, 'forte', 1.0, 'EDUCACAO',
               criterio, filho.atualizado_origem_em, true, :g
          FROM (
            SELECT lower(dados->>'pes_id') AS pes_id, dados, atualizado_origem_em
              FROM (
                SELECT DISTINCT ON (id_origem) dados, atualizado_origem_em
                  FROM bronze.registros
                 WHERE sistema_origem = 'CORESSO' AND tabela = 'PES_Pessoa' AND quarentena IS NULL
                 ORDER BY id_origem, id DESC
              ) u
             WHERE COALESCE(dados->>'pes_id', '') <> ''
          ) filho_raw
          JOIN (
            SELECT DISTINCT ON (pes_id) pes_id, cpf
              FROM (
                SELECT lower(dados->>'pes_id') AS pes_id, gold.cpf_valido_digitos(dados->>'psd_numero') AS cpf
                  FROM (
                    SELECT DISTINCT ON (id_origem) dados
                      FROM bronze.registros
                     WHERE sistema_origem = 'CORESSO' AND tabela = 'PES_PessoaDocumento' AND quarentena IS NULL
                     ORDER BY id_origem, id DESC
                  ) d
                 WHERE (
                   CASE WHEN dados->>'tdo_classe' ~ '^-?[0-9]+$' THEN (dados->>'tdo_classe')::int
                        WHEN dados->>'tdo_classificacao' ~ '^-?[0-9]+$' THEN (dados->>'tdo_classificacao')::int
                   END) = 1
              ) x
             WHERE cpf IS NOT NULL
             ORDER BY pes_id, cpf
          ) doc_filho ON doc_filho.pes_id = filho_raw.pes_id
          JOIN silver.identificadores ifilho ON ifilho.tipo = 'cpf' AND ifilho.valor = doc_filho.cpf
          JOIN LATERAL (SELECT filho_raw.pes_id, ifilho.cidadao_id, filho_raw.atualizado_origem_em, filho_raw.dados) filho ON true
          JOIN (
            SELECT campo, parentesco, criterio FROM (VALUES
              ('pes_idFiliacaoMae', 'mãe', 'pes_idFiliacaoMae'),
              ('pes_idFiliacaoPai', 'pai', 'pes_idFiliacaoPai')
            ) v(campo, parentesco, criterio)
          ) lig ON COALESCE(filho.dados->>lig.campo, '') <> ''
          JOIN (
            SELECT DISTINCT ON (pes_id) pes_id, cpf FROM (
              SELECT lower(dados->>'pes_id') AS pes_id, gold.cpf_valido_digitos(dados->>'psd_numero') AS cpf
                FROM (
                  SELECT DISTINCT ON (id_origem) dados
                    FROM bronze.registros
                   WHERE sistema_origem = 'CORESSO' AND tabela = 'PES_PessoaDocumento' AND quarentena IS NULL
                   ORDER BY id_origem, id DESC
                ) d
               WHERE (
                 CASE WHEN dados->>'tdo_classe' ~ '^-?[0-9]+$' THEN (dados->>'tdo_classe')::int
                      WHEN dados->>'tdo_classificacao' ~ '^-?[0-9]+$' THEN (dados->>'tdo_classificacao')::int
                 END) = 1
            ) x WHERE cpf IS NOT NULL
            ORDER BY pes_id, cpf
          ) doc_pai ON doc_pai.pes_id = lower(filho.dados->>lig.campo)
          JOIN silver.identificadores ipai ON ipai.tipo = 'cpf' AND ipai.valor = doc_pai.cpf
          JOIN LATERAL (SELECT ipai.cidadao_id) pai ON pai.cidadao_id <> filho.cidadao_id
         ORDER BY filho.cidadao_id, pai.cidadao_id, parentesco
        ON CONFLICT ON CONSTRAINT uq_gold_genealogia_aresta DO UPDATE SET
          confianca = EXCLUDED.confianca, criterio = EXCLUDED.criterio,
          fonte_atualizado_em = EXCLUDED.fonte_atualizado_em, ativo = true, geracao = EXCLUDED.geracao
    """), {"g": geracao})
    db.execute(text("""
        INSERT INTO gold.genealogia_arestas (
          origem, destino, sentido, parentesco, forca, confianca, base, criterio, fonte_atualizado_em, ativo, geracao)
        SELECT DISTINCT ON (a.cidadao_id, i.cidadao_id, sentido, parentesco)
               a.cidadao_id, i.cidadao_id, sentido, parentesco, 'forte', 1.0, 'EDUCACAO',
               'CPF do responsável (ACA_AlunoResponsavel)', a.atualizado_em, true, :g
          FROM silver.escola_responsaveis r
          JOIN silver.escola_alunos a ON a.alu_id = r.alu_id
          JOIN silver.identificadores i ON i.tipo = 'cpf' AND i.valor = gold.cpf_valido_digitos(r.cpf)
          JOIN LATERAL (
            SELECT CASE
                     WHEN r.tra_id = 1
                       OR gold.nome_norm(r.tipo) LIKE '%MAE%'
                       OR gold.nome_norm(r.tipo) LIKE '%PAI%'
                       OR gold.nome_norm(r.tipo) LIKE '%FILIAC%'
                     THEN 'ascendente' ELSE 'outro' END AS sentido,
                   left(COALESCE(NULLIF(btrim(r.tipo), ''),
                     CASE WHEN r.tra_id = 1
                            OR gold.nome_norm(r.tipo) LIKE '%MAE%'
                            OR gold.nome_norm(r.tipo) LIKE '%PAI%'
                            OR gold.nome_norm(r.tipo) LIKE '%FILIAC%'
                          THEN 'mãe/pai' ELSE 'responsável' END), 80) AS parentesco
          ) papel ON true
         WHERE r.excluido IS FALSE
           AND a.cidadao_id IS NOT NULL
           AND a.cidadao_id <> i.cidadao_id
         ORDER BY a.cidadao_id, i.cidadao_id, sentido, parentesco
        ON CONFLICT ON CONSTRAINT uq_gold_genealogia_aresta DO UPDATE SET
          confianca = EXCLUDED.confianca, criterio = EXCLUDED.criterio,
          fonte_atualizado_em = EXCLUDED.fonte_atualizado_em, ativo = true, geracao = EXCLUDED.geracao
    """), {"g": geracao})


def _fracas(db: Session, geracao: int, trgm: bool) -> None:
    fontes = (
        (_textos_bronze("Nome", "Cpf", "Dt_Nasc", "Mae", "Pai", "Cep", "Outra_Rua"),
         {"sistema": "SAUDE", "tabela": "pacientes", "base": "SAUDE", "g": geracao}),
        (_textos_bronze("FALECIDO", "", "DT_NASC", "MAE", "PAI", "", ""),
         {"sistema": "CEMITERIO", "tabela": "Obitos", "base": "CEMITERIO", "g": geracao}),
        (_MUNICIPES, {"g": geracao}),
    )
    sim = "similarity(texto_norm, nome_norm)" if trgm else "0::float8"
    for textos, params in fontes:
        db.execute(text(_EXATA.replace("/*TEXTOS*/", textos)), params)
        db.execute(text(_SIMILAR.replace("/*TEXTOS*/", textos).replace("/*SIM*/", sim)), params)


def materializar(db: Session) -> dict:
    """Recalcula pessoas e arestas. Pode repetir: a geração nova substitui a leitura, sem apagar a anterior."""
    db.execute(text("SET LOCAL statement_timeout = 0"))
    inicio = time.perf_counter()
    trgm = _tem_trgm(db)
    geracao = _geracao(db)
    _pessoas(db, geracao)
    _fortes(db, geracao)
    _fracas(db, geracao, trgm)
    arestas = int(db.scalar(text("SELECT count(*) FROM gold.genealogia_arestas WHERE geracao = :g"), {"g": geracao}) or 0)
    pessoas = int(db.scalar(text("SELECT count(*) FROM gold.genealogia_pessoas WHERE geracao = :g"), {"g": geracao}) or 0)
    segundos = round(time.perf_counter() - inicio, 3)
    db.execute(text("""
        INSERT INTO gold.genealogia_estado (id, geracao, arestas, segundos, trgm, populado_em)
        VALUES (1, :g, :n, :s, :trgm, now())
        ON CONFLICT (id) DO UPDATE SET
          geracao = EXCLUDED.geracao, arestas = EXCLUDED.arestas, segundos = EXCLUDED.segundos,
          trgm = EXCLUDED.trgm, populado_em = EXCLUDED.populado_em
    """), {"g": geracao, "n": arestas, "s": segundos, "trgm": trgm})
    db.execute(text("ANALYZE gold.genealogia_pessoas"))
    db.execute(text("ANALYZE gold.genealogia_arestas"))
    log.info("genealogia arestas=%s pessoas=%s segundos=%.3f trgm=%s geracao=%s",
             arestas, pessoas, segundos, trgm, geracao)
    return {"arestas": arestas, "pessoas": pessoas, "segundos": segundos, "geracao": geracao, "trgm": trgm}
