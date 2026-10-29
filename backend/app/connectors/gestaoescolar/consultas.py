"""Conector de educação: FRA_GestaoEscolar (aluno, matrícula, turma, escola, responsável) + FRA_CoreSSO (pessoa).

GE1/GE2/GE10/GE11:
- Só SELECT, montado aqui e validado por `garante_somente_leitura`; cada consulta roda pelo guard (ROLLBACK).
- Lista fechada de fontes (FONTES): tabela fora dela é recusada antes da rede. Colunas explícitas, nunca `*`.
- Toda tabela lida com WITH (NOLOCK), TOP (lote) e keyset pela chave primária (parametrizado).
- Tabelas e colunas sensíveis (saúde, deficiência, raça/cor, religião, indígena, socioeconômico, foto,
  SYS_Usuario*, InscricaoListaUsuariosErro) estão em EXCLUIDAS e são recusadas por `valida_fontes`.
- A pessoa do CoreSSO só é lida no escopo da escola (aluno, responsável ou mãe de aluno), pela junção direta
  entre os bancos (mesma instância) por pes_id.
- Credencial só por SIGPMF_SRC_GESTAOESCOLAR_USER / _PASSWORD; o login `sistema` é recusado.
"""
import re
from dataclasses import dataclass, field
from typing import Any

from app.connectors.sqlserver.connector import SqlServerConnector
from app.connectors.sqlserver.politicas import TabelaRecusada, coluna_proibida
from app.core.sql_guard import garante_somente_leitura

ESCOLAR = "GESTAOESCOLAR"
PESSOAS = "CORESSO"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

# GE10: nada disto é lido. Regex sem distinção de caixa, aplicada ao nome da tabela e da coluna.
TABELAS_EXCLUIDAS = (
    r"FichaMedica", r"IMC$", r"IndiceMassaCorporea", r"Vacinacao", r"AlunoRefeicao", r"Deficien", r"Transtorno",
    r"AtendimentoEspecial", r"Religiao", r"Indigena", r"ProgramaSocial", r"^PPE_", r"Socioeconomico", r"BolsaFamilia",
    r"^SYS_Usuario", r"^VW_SYS_Usuario", r"InscricaoListaUsuariosErro", r"^SYS_Grupo", r"^SYS_Perfil", r"^LOG_",
    r"^ARA_", r"Anexo", r"Ocorrencia", r"JustificativaFalta", r"AlunoAnotacao", r"AutorizadoBuscar", r"^REL_",
    r"^PES_PessoaDeficiencia", r"^PES_TipoDeficiencia", r"CamposAdicionais", r"^ACA_PessoaResponsavel$",
)
COLUNAS_EXCLUIDAS = frozenset({
    # CoreSSO PES_Pessoa / PES_PessoaDocumento
    "pes_racaCor", "pes_foto", "arq_idFoto", "pes_sobreMim", "pes_estadoCivil", "psd_infoComplementares",
    "psd_regiaoMilitar", "psd_numeroRA", "psd_tipoGuarda", "end_latitude", "end_longitude",
    # ACA_Aluno
    "rlg_id", "pin_id", "alu_observacao", "alu_rendaFamiliar", "alu_maeSituacaoViolencia",
    "alu_situacaoVulnerabilidadeSocial", "alu_tipoMobilidadeReduzida", "alu_tipoCuidador", "alu_tipoProfissionalSaude",
    "alu_tratamentoSaude", "alu_nivelSuporte", "alu_impactaDesenvolvimentoAprendizagem", "alu_Quilomba",
    "alu_circenseCigana", "alu_acolhimentoFamilias", "alu_responsavelInfoDoc", "alu_aulaReligiao",
    "alu_maeEstudanteEducacaoBasica", "alu_maeTrabalhaForaLar", "alu_imovel", "alu_qtdPessoasResidencia",
    "alu_posInternet", "alu_posNoteSmartPhoneTablet", "alu_justificativaDocFaltantes", "alu_autorizacaoUsoImagem",
    "alu_motivoNegacaoRematricula",
    # ACA_AlunoResponsavel
    "alr_rendaFamiliarMensal", "alr_programaSocial", "alr_despesaMoradia", "alr_localTrabalho", "alr_empresa",
    "alr_omitidoFormaLei", "alr_tipoGuarda", "alr_profissao", "alr_aposentado", "alr_pensionista",
    "alr_tipoConectividade", "alr_aparelhoAcessoInternet", "alr_horarioExpediente",
})
_COLUNAS_EXCLUIDAS_RE = (r"^alu_observacao", r"MobilidadeReduzida", r"^alu_responsavelInfo", r"^alu_qtdPessoasResidencia",
                         r"racaCor", r"foto", r"religi", r"indigena", r"deficien", r"saude", r"renda")


def coluna_excluida(nome: str) -> bool:
    n = nome.split(".")[-1]
    return (n.lower() in {c.lower() for c in COLUNAS_EXCLUIDAS} or coluna_proibida(n)
            or any(re.search(p, n, re.IGNORECASE) for p in _COLUNAS_EXCLUIDAS_RE))


def tabela_excluida(nome: str) -> bool:
    return any(re.search(p, nome.split(".")[-1], re.IGNORECASE) for p in TABELAS_EXCLUIDAS)


@dataclass(frozen=True)
class Fonte:
    """Uma consulta da carga: tabela principal (+ junções de domínio), colunas explícitas e chave do keyset.
    `colunas` são (expressão alias.coluna, nome de saída). A chave usa os nomes de saída."""
    nome: str                       # nome lógico gravado na bronze (= tabela principal)
    sistema: str                    # GESTAOESCOLAR | CORESSO
    banco: str                      # "escolar" | "pessoas"
    tabela: str
    alias: str
    colunas: tuple[tuple[str, str], ...]
    chave: tuple[str, ...]
    juncoes: tuple[tuple[str, str, str, str], ...] = ()  # (banco, tabela, alias, condição ON)
    filtro: str = ""                # condição fixa (literais inteiros, nunca texto de fora)
    escopo_pessoa: str = ""         # coluna alias.pes_id restrita ao escopo da escola
    campo_data: str | None = None
    tabelas_lidas: tuple[str, ...] = field(default=())
    tipos_documento: bool = False   # filtra d.tdo_id pelos tipos aceitos do catálogo (parâmetros), ver mapa_tipos
    # IN10a: outras datas de alteração que também trazem a linha na incremental (ex.: END_Endereco da junção).
    datas_extra: tuple[str, ...] = ()

    def todas_tabelas(self) -> tuple[str, ...]:
        return (self.tabela, *(j[1] for j in self.juncoes), *self.tabelas_lidas)


def _c(alias: str, *cols: str) -> tuple[tuple[str, str], ...]:
    return tuple((f"{alias}.{c}", c) for c in cols)


FONTES: tuple[Fonte, ...] = (
    # ---- FRA_GestaoEscolar ----
    Fonte("ACA_Aluno", ESCOLAR, "escolar", "ACA_Aluno", "a",
          _c("a", "alu_id", "pes_id", "end_id", "alu_situacao", "alu_dataAlteracao"), ("alu_id",),
          campo_data="alu_dataAlteracao"),
    Fonte("ACA_AlunoCurriculo", ESCOLAR, "escolar", "ACA_AlunoCurriculo", "c",
          _c("c", "alu_id", "alc_id", "alc_matricula", "esc_id", "uni_id", "cur_id", "crr_id", "crp_id", "alc_situacao",
             "alc_dataAlteracao"), ("alu_id", "alc_id"), campo_data="alc_dataAlteracao"),
    Fonte("MTR_MatriculaTurma", ESCOLAR, "escolar", "MTR_MatriculaTurma", "m",
          _c("m", "alu_id", "mtu_id", "alc_id", "tur_id", "cur_id", "crr_id", "crp_id", "mtu_situacao",
             "mtu_dataAlteracao"), ("alu_id", "mtu_id"), campo_data="mtu_dataAlteracao"),
    Fonte("TUR_TurmaCurriculo", ESCOLAR, "escolar", "TUR_TurmaCurriculo", "tc",
          _c("tc", "tur_id", "cur_id", "crr_id", "crp_id", "tcr_situacao", "tcr_dataAlteracao"),
          ("tur_id", "cur_id", "crr_id", "crp_id"), campo_data="tcr_dataAlteracao"),
    Fonte("TUR_Turma", ESCOLAR, "escolar", "TUR_Turma", "t",
          _c("t", "tur_id", "esc_id", "uni_id", "tur_codigo", "tur_situacao", "tur_dataAlteracao"), ("tur_id",),
          campo_data="tur_dataAlteracao"),
    Fonte("ESC_UnidadeEscola", ESCOLAR, "escolar", "ESC_UnidadeEscola", "u",
          _c("u", "esc_id", "uni_id", "uni_descricao", "uni_situacao", "uni_dataAlteracao"), ("esc_id", "uni_id"),
          campo_data="uni_dataAlteracao"),
    Fonte("ESC_Escola", ESCOLAR, "escolar", "ESC_Escola", "e",
          _c("e", "esc_id", "esc_nome", "esc_codigo", "esc_codigoInep", "esc_situacao", "esc_dataAlteracao"), ("esc_id",),
          campo_data="esc_dataAlteracao"),
    Fonte("ACA_AlunoResponsavel", ESCOLAR, "escolar", "ACA_AlunoResponsavel", "r",
          _c("r", "alu_id", "alr_id", "tra_id", "pes_id", "alr_principal", "alr_constaCertidaoNascimento",
             "alr_apenasFiliacao", "alr_moraComAluno", "alr_situacao", "alr_dataAlteracao"), ("alu_id", "alr_id"),
          campo_data="alr_dataAlteracao"),
    Fonte("ACA_TipoResponsavelAluno", ESCOLAR, "escolar", "ACA_TipoResponsavelAluno", "tr",
          _c("tr", "tra_id", "tra_nome", "tra_tipoResponsavelPadrao", "tra_situacao"), ("tra_id",)),
    # ---- FRA_CoreSSO (só pessoas no escopo da escola) ----
    Fonte("PES_Pessoa", PESSOAS, "pessoas", "PES_Pessoa", "p",
          _c("p", "pes_id", "pes_nome", "pes_nomeSocial", "pes_dataNascimento", "pes_sexo", "pes_idFiliacaoMae",
             "pes_idFiliacaoPai", "pes_situacao", "pes_dataAlteracao"), ("pes_id",),
          escopo_pessoa="p.pes_id", campo_data="pes_dataAlteracao"),
    # Documento alterado não muda pes_dataAlteracao: hash próprio por linha de documento.
    # tdo_classificacao é opcional no CoreSSO (NULL em bases antigas): o filtro não fica mais na junção.
    # O catálogo SYS_TipoDocumentacao (7 linhas) é lido antes; os tdo_id aceitos (mapa_tipos) entram como
    # parâmetros em `d.tdo_id IN (...)` e a classe derivada vai na coluna `tdo_classe` de cada linha.
    Fonte("PES_PessoaDocumento", PESSOAS, "pessoas", "PES_PessoaDocumento", "d",
          _c("d", "pes_id", "tdo_id", "psd_numero", "psd_situacao", "psd_dataAlteracao"), ("pes_id", "tdo_id"),
          escopo_pessoa="d.pes_id", campo_data="psd_dataAlteracao", tabelas_lidas=("SYS_TipoDocumentacao",),
          tipos_documento=True),
    Fonte("PES_CertidaoCivil", PESSOAS, "pessoas", "PES_CertidaoCivil", "cc",
          _c("cc", "pes_id", "ctc_id", "ctc_tipo", "ctc_modeloNovo", "ctc_matricula", "ctc_situacao", "ctc_dataAlteracao"),
          ("pes_id", "ctc_id"), filtro="cc.[ctc_tipo] = 1", escopo_pessoa="cc.pes_id", campo_data="ctc_dataAlteracao"),
    Fonte("PES_PessoaEndereco", PESSOAS, "pessoas", "PES_PessoaEndereco", "pe",
          _c("pe", "pes_id", "pse_id", "pse_numero", "pse_complemento", "pse_enderecoPrincipal", "pse_situacao",
             "pse_dataAlteracao") + (("en.end_cep", "end_cep"), ("en.end_logradouro", "end_logradouro"),
                                     ("en.end_bairro", "end_bairro")), ("pes_id", "pse_id"),
          juncoes=(("pessoas", "END_Endereco", "en", "en.[end_id] = pe.[end_id]"),),
          filtro="pe.[pse_enderecoPrincipal] = 1", escopo_pessoa="pe.pes_id", campo_data="pse_dataAlteracao",
          datas_extra=("en.end_dataAlteracao",)),
)
POR_NOME = {f.nome: f for f in FONTES}

# ---- Tipos de documento (CPF / PIS / NIS) ----
CLASSES_ACEITAS = (1, 3, 4)  # tdo_classificacao: 1 CPF, 3 PIS, 4 NIS (schema CoreSSO)
# Plano B (decisão final do Tech Lead, 29/10/2026): fora de 1/3/4, a classificação é ignorada (na origem os 7 tipos
# estão com 99) e decide a sigla (sem caixa, sem espaços nas pontas). Siglas na origem: CNPJ, CPF, RG, NIS, CIN, CNS e
# uma vazia. Aceitas: CPF, NIS, PIS e PIS/PASEP. CNS (saúde, fora da v1), CIN, CNPJ, RG e sigla vazia ficam de fora.
SIGLAS = {"CPF": 1, "PIS": 3, "PIS/PASEP": 3, "NIS": 4}
SEM_TIPO_DOCUMENTO = "sem_tipo_documento"
MSG_SEM_TIPO_DOCUMENTO = ("Nenhum tipo de documento CPF, PIS ou NIS foi encontrado no catálogo "
                          "SYS_TipoDocumentacao da origem; a leitura de documentos foi interrompida.")


class TiposDocumentoAusentes(RuntimeError):
    """Catálogo sem nenhum tipo aceito: motivo fixo (`codigo`), nunca o texto da exceção."""
    codigo = SEM_TIPO_DOCUMENTO

    def __init__(self) -> None:
        super().__init__(MSG_SEM_TIPO_DOCUMENTO)


def _int_ou_none(v) -> int | None:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def classe_do_tipo(tipo: dict) -> int | None:
    """Aceito se tdo_classificacao em (1, 3, 4) (plano A) OU se a sigla for CPF, NIS, PIS ou PIS/PASEP (plano B).
    Qualquer outra classificação é ignorada; CNS, CIN, RG, CNH, CNPJ e sigla vazia ficam de fora."""
    cl = _int_ou_none(tipo.get("tdo_classificacao"))
    if cl in CLASSES_ACEITAS:
        return cl
    return SIGLAS.get(str(tipo.get("tdo_sigla") or "").strip().upper())


def chave_tipo(tdo_id) -> str:
    return str(tdo_id).strip().lower()


def mapa_tipos(catalogo: list[dict]) -> tuple[tuple, dict[str, int]]:
    """(tdo_id aceitos, no tipo original do driver, para os parâmetros; {tdo_id normalizado: classe}).
    Nenhum aceito -> TiposDocumentoAusentes."""
    ids, mapa = [], {}
    for t in catalogo:
        cl = classe_do_tipo(t)
        if cl is not None and t.get("tdo_id") is not None:
            ids.append(t["tdo_id"])
            mapa[chave_tipo(t["tdo_id"])] = cl
    if not ids:
        raise TiposDocumentoAusentes()
    return tuple(ids), mapa


def anota_classe(linhas: list[dict], mapa: dict[str, int]) -> list[dict]:
    """Grava a classe derivada (`tdo_classe`) em cada linha de documento lida."""
    for r in linhas:
        r["tdo_classe"] = mapa.get(chave_tipo(r.get("tdo_id")))
    return linhas
TABELAS_PERMITIDAS = frozenset(t for f in FONTES for t in f.todas_tabelas()) | {"ACA_Aluno", "ACA_AlunoResponsavel"}


def valida_fontes(fontes=FONTES) -> None:
    """GE10: nenhuma tabela ou coluna excluída aparece em consulta alguma (falha no import e no teste)."""
    for f in fontes:
        for t in f.todas_tabelas():
            if tabela_excluida(t):
                raise TabelaRecusada(f"Tabela excluída pela LGPD na fonte {f.nome}: {t}")
        ruins = [saida for _e, saida in f.colunas if coluna_excluida(saida)]
        if ruins:
            raise TabelaRecusada(f"Colunas excluídas pela LGPD na fonte {f.nome}: {ruins}")
        saidas = [s for _e, s in f.colunas]
        if any(k not in saidas for k in f.chave):
            raise TabelaRecusada(f"Chave de {f.nome} fora das colunas.")


valida_fontes()


def _ident(nome: str) -> str:
    if not _IDENT.match(nome or ""):
        raise ValueError(f"Identificador inválido: {nome!r}")
    return f"[{nome}]"


def _col(expr: str) -> str:
    alias, col = expr.split(".")
    return f"{_ident(alias)}.{_ident(col)}"


def usuario_base(user) -> str:
    """Login sem domínio (B1): `DOMINIO\\sistema`, `sistema@dominio` e ` Sistema ` viram `sistema`."""
    u = str(user or "").strip().lower().split("\\")[-1].strip()
    return u.split("@")[0].strip()


@dataclass(repr=False)
class ConfigGE:
    host: str | None
    port: int
    user: str | None
    password: str | None
    banco_escolar: str
    banco_pessoas: str
    lote: int
    max_lotes: int
    amostra: int
    timeout: int = 5
    consulta_timeout: int = 120

    @classmethod
    def do_ambiente(cls, s) -> "ConfigGE":
        return cls(s.ge_host or s.sqlserver_host, int(s.ge_port or s.sqlserver_port), s.ge_user, s.ge_password,
                   s.ge_banco_escolar, s.ge_banco_pessoas, int(s.ge_lote), int(s.ge_max_lotes), int(s.ge_amostra),
                   int(s.sqlserver_timeout), int(s.sqlserver_consulta_timeout))

    def __repr__(self) -> str:  # nunca expõe host, usuário nem senha
        return (f"ConfigGE(host=***, user=***, password=***, banco_escolar={self.banco_escolar!r}, "
                f"banco_pessoas={self.banco_pessoas!r}, lote={self.lote}, max_lotes={self.max_lotes})")

    __str__ = __repr__

    def configurado(self) -> bool:
        return bool(self.host and self.user and self.password)

    def valida(self) -> None:
        """GE11: só a credencial própria do conector; o login `sistema` (da aplicação) é recusado."""
        if not self.configurado():
            raise PermissionError("Origem de educação não configurada (SIGPMF_SRC_GESTAOESCOLAR_USER/_PASSWORD).")
        if usuario_base(self.user) == "sistema":
            raise PermissionError("O usuário `sistema` não pode ser usado pelo conector de educação.")
        _ident(self.banco_escolar)
        _ident(self.banco_pessoas)


class ConsultasGE:
    """Monta os SELECT do conector (sem rede). A execução é do leitor (guard + ROLLBACK)."""

    def __init__(self, banco_escolar: str = "FRA_GestaoEscolar", banco_pessoas: str = "FRA_CoreSSO"):
        self.bancos = {"escolar": _ident(banco_escolar), "pessoas": _ident(banco_pessoas)}

    def _tab(self, banco: str, tabela: str) -> str:
        if tabela not in TABELAS_PERMITIDAS or tabela_excluida(tabela):
            raise TabelaRecusada(f"Tabela {tabela} fora da lista permitida do conector de educação.")
        return f"{self.bancos[banco]}.[dbo].{_ident(tabela)}"

    def escopo(self, coluna_pes: str) -> str:
        """Pessoa ligada à escola: aluno, responsável ou mãe (pes_idFiliacaoMae) de aluno. Junção direta entre bancos."""
        c = _col(coluna_pes)
        aluno, resp, pes = (self._tab("escolar", "ACA_Aluno"), self._tab("escolar", "ACA_AlunoResponsavel"),
                            self._tab("pessoas", "PES_Pessoa"))
        return (f"(EXISTS (SELECT 1 FROM {aluno} AS [xa] WITH (NOLOCK) WHERE [xa].[pes_id] = {c})"
                f" OR EXISTS (SELECT 1 FROM {resp} AS [xr] WITH (NOLOCK) WHERE [xr].[pes_id] = {c})"
                f" OR EXISTS (SELECT 1 FROM {aluno} AS [xm] WITH (NOLOCK) JOIN {pes} AS [xp] WITH (NOLOCK)"
                f" ON [xp].[pes_id] = [xm].[pes_id] WHERE [xp].[pes_idFiliacaoMae] = {c}))")

    def escopo_recente(self, coluna_pes: str) -> str:
        """Incremental: pessoa que ENTROU no escopo (aluno/responsável alterado desde a marca) é lida mesmo sem mudar
        a própria data. Devolve a condição (3 parâmetros `desde`, preenchidos por quem chama)."""
        c = _col(coluna_pes)
        aluno, resp, pes = (self._tab("escolar", "ACA_Aluno"), self._tab("escolar", "ACA_AlunoResponsavel"),
                            self._tab("pessoas", "PES_Pessoa"))
        return (f"EXISTS (SELECT 1 FROM {aluno} AS [ya] WITH (NOLOCK) WHERE [ya].[pes_id] = {c}"
                f" AND [ya].[alu_dataAlteracao] >= %s)"
                f" OR EXISTS (SELECT 1 FROM {resp} AS [yr] WITH (NOLOCK) WHERE [yr].[pes_id] = {c}"
                f" AND [yr].[alr_dataAlteracao] >= %s)"
                f" OR EXISTS (SELECT 1 FROM {aluno} AS [ym] WITH (NOLOCK) JOIN {pes} AS [yp] WITH (NOLOCK)"
                f" ON [yp].[pes_id] = [ym].[pes_id] WHERE [yp].[pes_idFiliacaoMae] = {c}"
                f" AND [ym].[alu_dataAlteracao] >= %s)")

    def tipos_documento(self) -> str:
        """Catálogo de tipos de documento (7 linhas na origem; sem dado pessoal)."""
        t = self._tab("pessoas", "SYS_TipoDocumentacao")
        return garante_somente_leitura(
            f"SELECT [td].[tdo_id], [td].[tdo_classificacao], [td].[tdo_sigla], [td].[tdo_validacao]"
            f" FROM {t} AS [td] WITH (NOLOCK)")

    def pagina(self, fonte: Fonte | str, limite: int, ultima: tuple | None = None,
               tdo_ids: tuple | None = None, desde=None) -> tuple[str, tuple]:
        """`desde` (datetime, só na incremental): `campo_data >= desde` OR datas_extra >= desde OR pessoa que entrou no
        escopo desde então. Sempre parâmetro, nunca concatenado. Fonte sem campo_data ignora `desde` (lida inteira)."""
        f = POR_NOME[fonte] if isinstance(fonte, str) else fonte
        if f.nome not in POR_NOME or POR_NOME[f.nome] != f:
            raise TabelaRecusada(f"Fonte {f.nome} fora da lista do conector de educação.")
        if isinstance(limite, bool) or not isinstance(limite, int) or limite < 1:
            raise ValueError(f"Lote inválido: {limite!r}")
        expr = dict((s, e) for e, s in f.colunas)
        cols = ", ".join(f"{_col(e)} AS {_ident(s)}" for e, s in f.colunas)
        sql = f"SELECT TOP ({int(limite)}) {cols} FROM {self._tab(f.banco, f.tabela)} AS {_ident(f.alias)} WITH (NOLOCK)"
        for banco, tabela, alias, on in f.juncoes:
            sql += f" JOIN {self._tab(banco, tabela)} AS {_ident(alias)} WITH (NOLOCK) ON {on}"
        chave_expr = tuple(expr[k] for k in f.chave)
        where, params = SqlServerConnector.filtro_keyset(chave_expr, ultima)
        conds = [where.removeprefix(" WHERE ")] if where else []
        if f.filtro:
            conds.append(f"({f.filtro})")
        if f.escopo_pessoa:
            conds.append(self.escopo(f.escopo_pessoa))
        params = tuple(params or ())
        if f.tipos_documento:  # só parâmetros (GUIDs do catálogo); nunca valor concatenado no SQL
            if not tdo_ids:
                raise TiposDocumentoAusentes()
            conds.append(f"{_ident(f.alias)}.[tdo_id] IN ({', '.join(['%s'] * len(tdo_ids))})")
            params += tuple(tdo_ids)
        if desde is not None and f.campo_data:
            alt = [f"{_col(expr[f.campo_data])} >= %s", *(f"{_col(d)} >= %s" for d in f.datas_extra)]
            n = len(alt)
            if f.escopo_pessoa:
                alt.append(self.escopo_recente(f.escopo_pessoa))
                n += 3
            conds.append("(" + " OR ".join(alt) + ")")
            params += (desde,) * n
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY " + ", ".join(_col(e) for e in chave_expr)
        return garante_somente_leitura(sql), params


class LeitorGE:
    """Leitor real: uma conexão pymssql com a credencial do conector; cada SELECT pelo guard (ROLLBACK, timeout)."""

    def __init__(self, config: ConfigGE):
        config.valida()
        self.config = config
        self.consultas = ConsultasGE(config.banco_escolar, config.banco_pessoas)
        self._conn = None
        self._tipos: tuple[tuple, dict[str, int]] | None = None  # catálogo lido uma vez por conexão

    def __enter__(self):
        from app.connectors.sqlserver.guardas import conecta_guardado
        c = self.config
        # R2 do #49: os dois bancos (escolar e pessoas, este via cross-database) passam pela lista permitida ANTES
        # de importar o driver ou abrir conexão.
        from app.connectors.sqlserver.politicas import autoriza_base
        escolar, pessoas = autoriza_base(c.banco_escolar), autoriza_base(c.banco_pessoas)
        self.consultas = ConsultasGE(escolar, pessoas)  # SQL cross-database só com os nomes canônicos da lista
        import pymssql  # import tardio: só com origem real
        self._conn = conecta_guardado(pymssql.connect, escolar, pessoas, server=c.host,
                                      port=int(c.port), user=c.user, password=c.password,
                                      login_timeout=int(c.timeout), timeout=int(c.consulta_timeout),
                                      autocommit=False)
        return self

    def __exit__(self, *exc):
        if self._conn is not None:
            self._conn.close()
        return False

    def ler(self, fonte: Fonte, ultima: tuple | None, limite: int, desde=None) -> list[dict[str, Any]]:
        from app.core.sql_guard import executa_somente_leitura
        if not fonte.tipos_documento:
            sql, params = self.consultas.pagina(fonte, limite, ultima, desde=desde)
            return executa_somente_leitura(self._conn, sql, params or None, limite=limite)
        if self._tipos is None:
            self._tipos = mapa_tipos(executa_somente_leitura(self._conn, self.consultas.tipos_documento(), limite=1000))
        ids, mapa = self._tipos
        sql, params = self.consultas.pagina(fonte, limite, ultima, tdo_ids=ids, desde=desde)
        return anota_classe(executa_somente_leitura(self._conn, sql, params or None, limite=limite), mapa)
