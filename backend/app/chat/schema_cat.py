"""Catálogo do chat lido do schema (bronze, silver, gold, ref) e dos comentários do Postgres.

Tabela ou coluna nova entra na próxima consulta (a assinatura muda) ou em até alguns minutos.
Tabelas internas não entram. CPF e documento não viram campo. Sem regra de domínio, a entidade
só aceita um número agregado.
"""
import logging
import re
import time

from sqlalchemy import BigInteger, Boolean, Column, MetaData, Numeric, String, Table, text
from sqlalchemy.orm import Session

from app.chat.catalogo import ENTIDADES, Campo, Entidade

log = logging.getLogger("sigpmf.chat")

SCHEMAS = ("bronze", "silver", "gold", "ref")
TTL_S = 180
_SIG: tuple | None = None
_QUANDO = 0.0

# Token inteiro (partido por _). "secretaria" não casa com "secret".
_NEGADA = frozenset({
    "usuario", "usuarios", "sessao", "sessoes", "session", "sessions",
    "auditoria", "audit", "senha", "senhas", "segredo", "segredos", "secret", "secrets",
    "parametro", "parametros", "token", "tokens", "credencial", "credenciais",
    "jwt", "auth", "login", "logins", "identificador", "identificadores",
})
# Nunca viram campo: o valor não sai nem para quem tem a tela.
_DOCUMENTO = frozenset({
    "cpf", "documento", "documentos", "cnpj", "nis", "certidao", "rg", "senha", "password", "passwd",
    "token", "secret", "segredo", "hash", "passaporte", "pis", "pasep", "matricula", "sus", "cartao",
})
# Entram no catálogo só quando a entidade tem domínio (a mesma secretaria da tela). Sem domínio, somem.
_SENSIVEL = frozenset({
    "email", "telefone", "celular", "fone", "contato", "whatsapp", "fax",
    "endereco", "logradouro", "cep", "complemento", "bairro",
    "saude", "diagnostico", "prontuario", "doenca", "medicamento", "clinico", "laudo",
    "nome", "mae", "pai", "nascimento", "social", "cidadao",
})
_REGRAS = (
    ({"escola", "educacao", "aluno", "alunos", "matricula", "matriculas", "professor", "turma"}, "educacao"),
    ({"atendimento", "atendimentos", "vacinacao", "vacinacoes", "agendamento", "agendamentos", "saude"}, "saude"),
    ({"cemiterio", "obito", "obitos", "falecido", "falecidos", "sepultura"}, "cemiterio"),
    ({"sei"}, "sei"),
    ({"divida", "dividas"}, "divida"),
    ({"empresa", "empresas"}, "sil"),
    ({"lancamento", "lancamentos", "imovel", "imoveis", "iptu", "iss", "tributo", "tributos"}, "iptu"),
    ({"financeiro"}, "financeiro"),
)
_TIPOS_INT = frozenset({"smallint", "integer", "bigint"})
_TIPOS_NUM = frozenset({"numeric", "decimal", "real", "double precision", "money"})
_TIPOS_TXT = frozenset({
    "character varying", "character", "text", "uuid", "date",
    "timestamp without time zone", "timestamp with time zone",
    "time without time zone", "time with time zone",
})
_SA = {"inteiro": BigInteger, "numero": Numeric, "bool": Boolean, "texto": String}

_FOLHA = {
    "ano": "Ano da competência.",
    "mes": "Mês da competência; 13 é o décimo terceiro.",
    "nome": "Nome da pessoa. Só sai para quem já vê a folha daquela secretaria.",
    "cargo": "Cargo na competência.",
    "secretaria": "Secretaria padronizada (a mesma do painel de RH).",
    "grupo": "ativa (secretarias), inativos ou outros.",
    "situacao": "Situação funcional publicada.",
    "salario_base": "Salário base do mês.",
    "vencimentos": "Total de vencimentos (bruto) de quem foi pago no mês.",
    "descontos": "Total de descontos do mês.",
    "liquido": "Total líquido do mês.",
}
_EMPRESA = {
    "nome": "Nome ou razão social, sem documento.",
    "tipo_pessoa": "PF ou PJ, sem CPF e sem CNPJ.",
    "possui_debito": "Se a empresa está marcada com débito.",
    "bairro": "Bairro do estabelecimento.",
}
_DIVIDA = {
    "ano": "Ano da inscrição.",
    "natureza": "Código da natureza do débito.",
    "saldo": "Saldo líquido em aberto.",
    "em_aberto": "Débito em aberto (não extinto, saldo positivo).",
    "extinta": "Inscrição extinta.",
    "protestos": "Quantidade de protestos.",
}


def tokens(nome: str) -> set[str]:
    return {p for p in re.split(r"[^a-z0-9]+", (nome or "").lower()) if p}


def tabela_negada(nome: str) -> bool:
    return bool(tokens(nome) & _NEGADA)


def invalidar() -> None:
    """A próxima consulta relê o schema. A carga nova muda a assinatura mesmo sem esta chamada."""
    global _SIG, _QUANDO
    _SIG = None
    _QUANDO = 0.0


def carregar(db: Session, destino: dict[str, Entidade]) -> dict[str, Entidade]:
    global _SIG, _QUANDO
    sig = _assinatura(db)
    agora = time.monotonic()
    if destino and sig == _SIG and (agora - _QUANDO) < TTL_S:
        return destino
    novo = _montar(db)
    destino.clear()
    destino.update(novo)
    _SIG = sig
    _QUANDO = agora
    log.info("chat catalogo %s entidades", len(destino))
    return destino


def aquecer() -> None:
    from app.core.db import SessionLocal
    db = SessionLocal()
    try:
        carregar(db, ENTIDADES)
    finally:
        db.close()


def _assinatura(db: Session) -> tuple:
    tabs = tuple(db.execute(text(
        "SELECT n.nspname, c.relname FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname IN ('bronze', 'silver', 'gold', 'ref') AND c.relkind = 'r' "
        "ORDER BY 1, 2")).all())
    marca = db.execute(text("SELECT COALESCE(MAX(id), 0) FROM controle.execucoes")).scalar()
    return (tabs, int(marca or 0))


def _montar(db: Session) -> dict[str, Entidade]:
    linhas = db.execute(text(
        "SELECT n.nspname AS schema, c.relname AS tabela, a.attname AS coluna, "
        "format_type(a.atttypid, a.atttypmod) AS formato, "
        "pg_catalog.col_description(a.attrelid, a.attnum) AS col_coment, "
        "pg_catalog.obj_description(c.oid) AS tab_coment "
        "FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname IN ('bronze', 'silver', 'gold', 'ref') AND c.relkind = 'r' "
        "AND a.attnum > 0 AND NOT a.attisdropped "
        "ORDER BY n.nspname, c.relname, a.attnum")).mappings().all()
    grupos: dict[tuple[str, str], dict] = {}
    for row in linhas:
        if tabela_negada(row["tabela"]):
            continue
        chave = (row["schema"], row["tabela"])
        grupo = grupos.get(chave)
        if grupo is None:
            grupo = {"coment": row["tab_coment"], "cols": []}
            grupos[chave] = grupo
        grupo["cols"].append(row)
    meta = MetaData()
    saida: dict[str, Entidade] = {}
    for (schema, tabela), grupo in grupos.items():
        tabela_sql, spec = _tabela(meta, schema, tabela, grupo["cols"])
        if tabela_sql is None:
            continue
        for pol in _politicas(schema, tabela, grupo["coment"]):
            campos = _campos(pol, spec, tabela_sql)
            if not campos:
                continue
            saida[pol["id"]] = Entidade(
                pol["id"], pol["rotulo"], pol["significado"], tabela_sql, campos,
                folha=pol["folha"], vigente=pol["vigente"], fonte_id=pol["fonte_id"],
                sistema=pol["sistema"], secretaria=pol["secretaria"],
                somente_agregados=pol["somente_agregados"])
    return saida


def _tabela(meta: MetaData, schema: str, tabela: str, cols: list) -> tuple[Table | None, dict]:
    spec = {}
    colunas = []
    for row in cols:
        tipo = _tipo(row["formato"])
        if tipo is None:
            continue
        classe = _classe(row["coluna"], row["col_coment"])
        if classe == "oculta":
            continue
        spec[row["coluna"]] = {"tipo": tipo, "sensivel": classe == "sensivel", "coment": row["col_coment"]}
        colunas.append(Column(row["coluna"], _SA[tipo]))
    if not colunas:
        return None, {}
    return Table(tabela, meta, *colunas, schema=schema), spec


def _campos(pol: dict, spec: dict, tabela: Table) -> dict[str, Campo]:
    campos = {}
    for nome, info in spec.items():
        if pol["somente_agregados"] and info["sensivel"]:
            continue
        tipo = info["tipo"]
        texto = (pol["textos"].get(nome) if pol["textos"] else None) or _curto(info["coment"]) or _legivel(nome)
        campos[nome] = Campo(
            nome, texto, tipo, tabela.c[nome],
            agrupavel=tipo in ("texto", "inteiro", "bool"),
            metrica=tipo in ("inteiro", "numero"),
            nominal=nome in pol["nominal"],
            sensivel=info["sensivel"])
    return campos


def _politicas(schema: str, tabela: str, coment) -> list[dict]:
    if (schema, tabela) == ("silver", "rh_remuneracao"):
        base = "Folha publicada no portal. O Senior ainda não tem tabela própria."
        return [
            _pol("folha_senior", "Folha vigente",
                 "Folha vigente: último mês comum publicado no portal. O Senior ainda não tem tabela própria.",
                 folha=True, vigente=True, sistema="PORTAL_RH", secretaria="secretaria",
                 textos=_FOLHA, nominal=frozenset({"nome"})),
            _pol("folha_legada", "Folha (histórico)",
                 "Histórico da folha publicada no portal, todos os meses, inclusive o 13º. " + base,
                 folha=True, sistema="PORTAL_RH", secretaria="secretaria",
                 textos=_FOLHA, nominal=frozenset({"nome"})),
        ]
    if (schema, tabela) == ("silver", "empresas_sil"):
        return [_pol("empresas", "Empresas (SIL)",
                     "Empresas da prata da SIL. Sem CPF e sem CNPJ.",
                     fonte_id="sil", textos=_EMPRESA)]
    if (schema, tabela) == ("silver", "dividas"):
        return [_pol("divida", "Dívida ativa",
                     "Inscrições em dívida ativa. Sem contribuinte, sem CPF e sem inscrição de imóvel.",
                     fonte_id="divida", textos=_DIVIDA)]
    fonte = _fonte(tabela)
    texto = _curto(coment) or _legivel(tabela)
    return [_pol(f"{schema}.{tabela}", texto[:80], texto, fonte_id=fonte, somente_agregados=fonte is None)]


def _pol(eid, rotulo, significado, *, folha=False, vigente=False, fonte_id=None, sistema=None,
         secretaria=None, somente_agregados=False, textos=None, nominal=frozenset()) -> dict:
    return {"id": eid, "rotulo": rotulo, "significado": _curto(significado) or rotulo,
            "folha": folha, "vigente": vigente, "fonte_id": fonte_id, "sistema": sistema,
            "secretaria": secretaria, "somente_agregados": somente_agregados,
            "textos": textos, "nominal": nominal}


def _fonte(tabela: str) -> str | None:
    toks = tokens(tabela)
    for chaves, fonte in _REGRAS:
        if toks & chaves:
            return fonte
    if tabela.startswith("sei_"):
        return "sei"
    return None


def _classe(coluna: str, coment) -> str:
    toks = tokens(coluna)
    nota = (coment or "").lower()
    if toks & _DOCUMENTO or any(p in nota for p in ("cpf", "cnpj", "senha", "segredo")):
        return "oculta"
    if toks & _SENSIVEL or any(p in nota for p in ("telefone", "e-mail", "email", "endereço", "endereco",
                                                    "prontuário", "prontuario", "diagnóstico", "diagnostico")):
        return "sensivel"
    return "normal"


def _tipo(formato: str) -> str | None:
    base = (formato or "").split("(")[0].strip().lower()
    if not base or base.endswith("[]"):
        return None
    if base in _TIPOS_INT:
        return "inteiro"
    if base in _TIPOS_NUM:
        return "numero"
    if base == "boolean":
        return "bool"
    if base in _TIPOS_TXT:
        return "texto"
    return None


def _legivel(nome: str) -> str:
    s = (nome or "").replace("_", " ").strip()
    return (s[:1].upper() + s[1:]) if s else nome


def _curto(texto, n=160) -> str | None:
    if not isinstance(texto, str):
        return None
    s = " ".join(texto.split())
    return s[:n] if s else None
