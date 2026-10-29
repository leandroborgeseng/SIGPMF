"""Catálogo das contagens do J1 (Q1–Q11, Q-A1–Q-A9, % de CPF inválido) — só agregados.

Cada consulta declara as colunas que pode devolver. Colunas `rotulo` só aceitam rótulos curtos do próprio SQL
(ex.: 'Insc_Imob', 'PF'); o resto tem de ser número. Qualquer outra coisa invalida o resultado (`resultado_nao_agregado`).
As consultas do SQL Server passam pelo `sql_guard` (um único SELECT/WITH, sem DML/DDL, sem `;`).
Os nomes marcados "ajustar" no plano ficam com `ajustar=True` e só rodam depois de o DBA confirmar (CONTAGENS_ATIVAS).
"""
from dataclasses import dataclass

from app.connectors.sqlserver.politicas import POLITICAS, normaliza_coluna


@dataclass(frozen=True)
class Consulta:
    id: str
    base: str            # banco do SQL Server, ou "postgres" (bronze/prata própria, transação READ ONLY)
    sql: str
    numericas: tuple[str, ...]
    rotulos: tuple[str, ...] = ()
    ajustar: bool = False  # nome de tabela ainda não confirmado pelo DBA


def _sql(s: str) -> str:
    return " ".join(s.split())


CONSULTAS: tuple[Consulta, ...] = (
    Consulta("Q1", "IPTU", _sql("""SELECT COUNT(*) AS total,
        SUM(CASE WHEN Cadastro IS NULL OR LTRIM(RTRIM(Cadastro))='' THEN 1 ELSE 0 END) AS nulos,
        SUM(CASE WHEN LEN(LTRIM(RTRIM(Cadastro)))=14 AND Cadastro NOT LIKE '%[^0-9]%' THEN 1 ELSE 0 END) AS ok_14dig
        FROM dbo.Cadastro WITH (NOLOCK)"""), ("total", "nulos", "ok_14dig"), ajustar=True),
    Consulta("Q2", "Geral", _sql("""SELECT COUNT(*) total, SUM(CASE WHEN CPF IS NULL OR CPF='' THEN 1 ELSE 0 END) nulos,
        SUM(CASE WHEN LEN(CPF)=11 AND CPF NOT LIKE '%[^0-9]%' THEN 1 ELSE 0 END) onze_digitos,
        SUM(CASE WHEN CPF LIKE REPLICATE(LEFT(CPF,1),11) THEN 1 ELSE 0 END) repetidos_dummy
        FROM dbo.Municipes WITH (NOLOCK)"""), ("total", "nulos", "onze_digitos", "repetidos_dummy"), ajustar=True),
    Consulta("Q3", "Geral", _sql("""WITH d AS (SELECT CPF FROM dbo.Municipes WITH (NOLOCK) WHERE LEN(CPF)=11
        GROUP BY CPF HAVING COUNT(*)>1) SELECT COUNT(*) AS cpfs_duplicados FROM d"""), ("cpfs_duplicados",),
             ajustar=True),
    Consulta("Q5", "Central", _sql("""SELECT COUNT(*) total, SUM(CASE WHEN cpf IS NULL THEN 1 ELSE 0 END) nulos,
        SUM(CASE WHEN cpf < 10000000000 THEN 1 ELSE 0 END) menos_de_11_digitos FROM dbo.Municipes WITH (NOLOCK)"""),
             ("total", "nulos", "menos_de_11_digitos"), ajustar=True),
    Consulta("Q7", "Divida", _sql("""WITH dv AS (SELECT DISTINCT CODI c FROM dbo.Divida WITH (NOLOCK))
        SELECT COUNT(*) codis_divida, SUM(CASE WHEN LEN(c)=14 THEN 1 ELSE 0 END) com_14 FROM dv"""),
             ("codis_divida", "com_14")),
    Consulta("Q9", "CadastroUnico", _sql("""SELECT COUNT(*) total,
        SUM(CASE WHEN nis IS NOT NULL AND LEN(nis)>=11 THEN 1 ELSE 0 END) com_nis,
        SUM(CASE WHEN cpfCnpj IS NOT NULL THEN 1 ELSE 0 END) com_cpf FROM dbo.cad_CadastroSimples WITH (NOLOCK)"""),
             ("total", "com_nis", "com_cpf")),
    Consulta("Q10", "DBSENIOR", _sql("""SELECT COUNT(*) servidores, SUM(CASE WHEN numcpf IS NULL THEN 1 ELSE 0 END) sem_cpf,
        SUM(CASE WHEN numcpf < 10000000000 THEN 1 ELSE 0 END) cpf_menos_11_digitos FROM dbo.r034fun WITH (NOLOCK)"""),
             ("servidores", "sem_cpf", "cpf_menos_11_digitos")),
    Consulta("Q11", "IPTU", _sql("""SELECT SUM(CASE WHEN LEN(x)=11 THEN 1 ELSE 0 END) parece_cpf,
        SUM(CASE WHEN LEN(x)=14 THEN 1 ELSE 0 END) parece_cnpj, SUM(CASE WHEN LEN(x) NOT IN (11,14) THEN 1 ELSE 0 END) outro,
        COUNT(*) total FROM (SELECT REPLACE(REPLACE(REPLACE(Documento,'.',''),'-',''),'/','') x
        FROM dbo.Cadastro WITH (NOLOCK)) q"""), ("parece_cpf", "parece_cnpj", "outro", "total"), ajustar=True),
    Consulta("Q-A2", "IPTU", _sql("""SELECT COUNT(*) total,
        SUM(CASE WHEN LEN(Insc_Imob)=14 AND Insc_Imob NOT LIKE '%[^0-9]%' THEN 1 ELSE 0 END) insc_14,
        SUM(CASE WHEN Insc_Imob = Cadastro THEN 1 ELSE 0 END) insc_igual_cadastro FROM dbo.Cadastro WITH (NOLOCK)"""),
             ("total", "insc_14", "insc_igual_cadastro")),
    Consulta("Q-A3", "ISS", _sql("""SELECT LEN(CAST(Inscricao_Municipal AS varchar(12))) digitos, COUNT(*) qtd
        FROM dbo.Contribuintes WITH (NOLOCK) GROUP BY LEN(CAST(Inscricao_Municipal AS varchar(12)))"""),
             ("digitos", "qtd")),
    Consulta("Q-A5", "Central", _sql("""SELECT LEN(REPLACE(REPLACE(REPLACE(NumProcesso,'.',''),'/',''),'-','')) digitos,
        COUNT(*) qtd FROM dbo.CertidoesProcessosSEI WITH (NOLOCK)
        GROUP BY LEN(REPLACE(REPLACE(REPLACE(NumProcesso,'.',''),'/',''),'-',''))"""), ("digitos", "qtd")),
    # Postgres (bronze/prata própria). Q-A9 roda aqui, nunca com tabela de apoio no SQL Server (§11.3 item 9).
    Consulta("Q-A6", "postgres", _sql("""SELECT length(regexp_replace(dados->>'InscrFisico','\\D','','g')) AS digitos,
        count(*) AS qtd FROM bronze.registros WHERE sistema_origem='sil' AND tabela='imoveis'
        AND ambiente_origem='producao' GROUP BY 1 ORDER BY 1"""), ("digitos", "qtd")),
    Consulta("Q-A8", "postgres", _sql("""SELECT tipo_pessoa, count(ccm) qtd, count(cidadao_id) com_cidadao,
        sum(CASE WHEN length(documento) NOT IN (11,14) THEN 1 ELSE 0 END) doc_comprimento_invalido
        FROM silver.empresas_sil GROUP BY tipo_pessoa"""), ("qtd", "com_cidadao", "doc_comprimento_invalido"),
             ("tipo_pessoa",)),
    Consulta("Q-A9", "postgres", _sql("""WITH a AS (SELECT DISTINCT NULLIF(regexp_replace(dados->>'NroInscricao','\\D','','g'),'')::bigint im
        FROM bronze.registros WHERE sistema_origem IN ('alvara','ALVARA') AND dados->>'NroInscricao' IS NOT NULL),
        c AS (SELECT ccm::bigint ccm FROM silver.empresas_sil)
        SELECT count(im) alvaras_com_inscricao,
        count(im) FILTER (WHERE im IN (SELECT ccm FROM c)) casa_direto,
        count(im) FILTER (WHERE im IN (SELECT ccm / 10 FROM c)) casa_ccm_div10 FROM a"""),
             ("alvaras_com_inscricao", "casa_direto", "casa_ccm_div10")),
    Consulta("Q4", "FRA_CoreSSO", _sql("""WITH g AS (SELECT DISTINCT CPF c FROM Geral.dbo.Municipes WITH (NOLOCK) WHERE LEN(CPF)=11),
        s AS (SELECT DISTINCT RIGHT('00000000000'+REPLACE(REPLACE(d.psd_numero,'.',''),'-',''),11) c
        FROM dbo.PES_PessoaDocumento d WITH (NOLOCK) JOIN dbo.SYS_TipoDocumentacao t WITH (NOLOCK) ON t.tdo_id=d.tdo_id
        WHERE t.tdo_classificacao=1)
        SELECT (SELECT COUNT(*) FROM s) cpfs_coresso, (SELECT COUNT(*) FROM s WHERE c IN (SELECT c FROM g)) em_ambos"""),
             ("cpfs_coresso", "em_ambos"), ajustar=True),
    Consulta("Q6", "ISS", _sql("""WITH i AS (SELECT DISTINCT Inscricao_Municipal im FROM dbo.Contribuintes WITH (NOLOCK)),
        s AS (SELECT DISTINCT Inscricao_Municipal im FROM Simples_Nacional.dbo.Empresas WITH (NOLOCK)
        WHERE Inscricao_Municipal IS NOT NULL)
        SELECT (SELECT COUNT(*) FROM s) simples, (SELECT COUNT(*) FROM s WHERE im IN (SELECT im FROM i)) casam_iss"""),
             ("simples", "casam_iss"), ajustar=True),
    Consulta("Q8", "FRA_CoreSSO", _sql("""SELECT COUNT(*) total,
        SUM(CASE WHEN e.end_cep IN (SELECT Cep FROM Correios.dbo.LOG_LOGRADOURO_SP WITH (NOLOCK)) THEN 1 ELSE 0 END) cep_no_dne
        FROM dbo.END_Endereco e WITH (NOLOCK)"""), ("total", "cep_no_dne"), ajustar=True),
    Consulta("Q-A1", "IPTU", _sql("""SELECT 'Insc_Imob' campo, LEN(LTRIM(RTRIM(Insc_Imob))) comprimento, COUNT(*) qtd
        FROM dbo.Cadastro WITH (NOLOCK) GROUP BY LEN(LTRIM(RTRIM(Insc_Imob)))
        UNION ALL SELECT 'Cadastro', LEN(LTRIM(RTRIM(Cadastro))), COUNT(*) FROM dbo.Cadastro WITH (NOLOCK)
        GROUP BY LEN(LTRIM(RTRIM(Cadastro)))"""), ("comprimento", "qtd"), ("campo",)),
    Consulta("Q-A7", "postgres", _sql("""WITH s AS (SELECT DISTINCT regexp_replace(dados->>'InscrFisico','\\D','','g') k
        FROM bronze.registros WHERE sistema_origem='sil' AND tabela='imoveis' AND ambiente_origem='producao'),
        i AS (SELECT DISTINCT lpad(regexp_replace(dados->>'Cadastro','\\D','','g'),14,'0') k
        FROM bronze.registros WHERE sistema_origem IN ('iptu','IPTU') AND tabela='Cadastro')
        SELECT (SELECT count(k) FROM s) sil, (SELECT count(k) FROM s WHERE k IN (SELECT k FROM i)) casa_cadastro"""),
             ("sil", "casa_cadastro")),
)

ROTULO_OK = r"^[A-Za-z_]{1,20}$"


def colunas_cpf() -> list[tuple[str, str, str]]:
    """(base, tabela, coluna) de CPF a partir das políticas já aprovadas (allowlist): nada fora delas é lido."""
    out = []
    for pol in POLITICAS.values():
        if pol.sistema == "SIL":  # API, não SQL Server
            continue
        for tab, cols in pol.colunas.items():
            for c in cols:
                if normaliza_coluna(c) in ("cpf", "nr_cpf", "num_cpf", "numcpf"):
                    out.append((pol.base, tab, c))
    return out


def _norm_cpf_sql(col: str) -> str:
    return f"RIGHT('00000000000' + REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(CAST([{col}] AS varchar(20)))),'.',''),'-',''),' ',''), 11)"


def _soma_ponderada(d: list[str], pesos: range) -> str:
    """Σ d_i·p_i sem o caractere `*` (o sql_guard recusa `*` fora de COUNT(*)): cada produto vira soma repetida."""
    return " + ".join(" + ".join([d[i]] * p) for i, p in enumerate(pesos))


def _dv_ok(soma: str, digito: str) -> str:
    """DV do CPF: resto = soma % 11; DV = 0 se resto < 2, senão 11 - resto."""
    return f"(CASE WHEN ({soma}) % 11 < 2 THEN 0 ELSE 11 - ({soma}) % 11 END) = {digito}"


def _cpf_dv_sql(x: str) -> str:
    """Condição SQL (portável T-SQL/Postgres) de DV válido para `x` já com 11 dígitos."""
    d = [f"CAST(SUBSTRING({x},{i},1) AS int)" for i in range(1, 12)]
    s1, s2 = _soma_ponderada(d, range(10, 1, -1)), _soma_ponderada(d, range(11, 1, -1))
    return f"{_dv_ok(s1, d[9])} AND {_dv_ok(s2, d[10])}"


def _cpf_valido_sql(x: str) -> str:
    """Expressão T-SQL: 1 se `x` tem 11 dígitos, não é repetido e o DV confere; só aritmética, sem função de usuário."""
    return (f"CASE WHEN {x} LIKE '%[^0-9]%' OR LEN({x}) <> 11 OR {x} = REPLICATE(LEFT({x},1),11) THEN 0 "
            f"WHEN {_cpf_dv_sql(x)} THEN 1 ELSE 0 END")


def sql_pct_cpf_invalido(tabela: str, coluna: str) -> str:
    return _sql(f"""SELECT COUNT(*) total, SUM(CASE WHEN c IS NULL THEN 1 ELSE 0 END) nulos,
        SUM(CASE WHEN c IS NOT NULL AND {_cpf_valido_sql("c")} = 0 THEN 1 ELSE 0 END) invalidos
        FROM (SELECT CASE WHEN [{coluna}] IS NULL THEN NULL ELSE {_norm_cpf_sql(coluna)} END c
        FROM dbo.[{tabela}] WITH (NOLOCK)) q""")


def sql_cpf_repetido(tabela: str, coluna: str, limiar: int, top: int) -> str:
    """J4: só devolve CPFs com COUNT >= limiar (HAVING no próprio SQL): os abaixo do corte nem saem da origem."""
    return _sql(f"""SELECT TOP ({int(top)}) c cpf, COUNT(*) ocorrencias FROM (SELECT {_norm_cpf_sql(coluna)} c
        FROM dbo.[{tabela}] WITH (NOLOCK) WHERE [{coluna}] IS NOT NULL) q GROUP BY c
        HAVING COUNT(*) >= {int(limiar)} ORDER BY COUNT(*) DESC""")
