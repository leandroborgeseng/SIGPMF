"""Políticas por sistema de origem (aceites S1/S3 da Saúde e CM1/CM2 do Cemitério).

- Só tabelas da lista permitida são lidas; o resto é recusado (e a recusa é auditada por quem chama).
- Para cada tabela, só as colunas listadas entram no SELECT. Colunas sensíveis nunca entram.
"""
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta


class TabelaRecusada(ValueError):
    """Recusa da política. `codigo` é fixo (vai para API/auditoria); a mensagem fica só no log interno."""

    def __init__(self, mensagem: str, codigo: str = "tabela_recusada"):
        super().__init__(mensagem)
        self.codigo = codigo


class BaseRecusada(TabelaRecusada):
    pass


# PR-04 A2: os 20 bancos de descarte (plano-aproveitamento, "Vazio/descartar") e o Internet (holerites, sensível)
# nunca entram em política nem em leitura, mesmo que alguém cadastre uma política apontando para eles.
BASES_DESCARTE = frozenset({
    "API_Usuario", "AtendSaude", "DbPPrioritariosIdentityMunicipios", "DBSENIOR_PORTAL", "DWConfiguration",
    "DWDiagnostics", "DWQueue", "EFWeb", "FRA_AVA_API", "FRA_CoreSSO_Log", "FRA_OauthProvider", "IPTU_Isencao",
    "LogSistemas", "LogSistemas_Anteriores", "LogSistemas_Ate2014", "Novo_Cemiterio", "Senhas", "Telefonia",
    "Traces", "TrendVP"})
BASES_SENSIVEIS_NUNCA = frozenset({"Internet"})
BASES_NUNCA = BASES_DESCARTE | BASES_SENSIVEIS_NUNCA


# R1 do QA do #49: além da lista negra, uma LISTA PERMITIDA. Só estes bancos podem ser abertos ou citados
# (Base.schema.Tabela). Banco novo entra aqui por PR, nunca por variável de ambiente.
BASES_PERMITIDAS = frozenset({
    "Saude", "Cemiterio", "ISS", "IPTU", "Divida", "Financeiro", "API SIL",  # conectores SQL/REST (POLITICAS)
    "FRA_GestaoEscolar", "FRA_CoreSSO",                                    # educação (LeitorGE)
    "Correios"})                                                           # DNE/CEP (#47)
_DECORACAO = "[]\"'`;"
SUFIXO_BASE_TESTE = "_test"  # só com APP_ENV=test: bancos sintéticos dos testes (ex.: zz_sintetico_test)


def normaliza_base(base) -> str:
    """Forma canônica do nome do banco: NFKC (tira largura total), sem caracteres invisíveis (categoria Cf:
    zero-width, BOM, marcas de direção), sem espaços/colchetes/aspas/; nas pontas, casefold."""
    import unicodedata
    s = unicodedata.normalize("NFKC", str(base))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    anterior = None
    while anterior != s:
        anterior = s
        s = s.strip().strip(_DECORACAO)
    return s.casefold()


_BASES_NUNCA_NORM = frozenset(normaliza_base(b) for b in BASES_NUNCA)
_BASES_PERMITIDAS_NORM = frozenset(normaliza_base(b) for b in BASES_PERMITIDAS)


def _ambiente_teste() -> bool:
    try:
        from app.core.config import get_settings
        return get_settings().app_env == "test"
    except Exception:  # noqa: BLE001 - sem configuração: nunca libera banco de teste
        return False


def autoriza_base(base: str) -> None:
    """A2 + R1 (#49): recusa banco de descarte/Internet (base_descartada), nome com decoração, caractere invisível
    ou largura total (base_nome_invalido) e qualquer banco fora de BASES_PERMITIDAS (base_fora_da_lista).
    Aceita só o nome limpo (sem diferença de caixa), opcionalmente entre um par de colchetes."""
    norm = normaliza_base(base)
    if norm in _BASES_NUNCA_NORM:
        raise BaseRecusada(f"Banco {base!r} fora do plano (descarte/sensível).", "base_descartada")
    cru = str(base)
    if cru.startswith("[") and cru.endswith("]"):
        cru = cru[1:-1]
    if not norm or cru.casefold() != norm:
        raise BaseRecusada(f"Nome de banco inválido: {base!r}.", "base_nome_invalido")
    if norm in _BASES_PERMITIDAS_NORM:
        return
    if norm.endswith(SUFIXO_BASE_TESTE) and _ambiente_teste():
        return
    raise BaseRecusada(f"Banco {base!r} fora da lista permitida.", "base_fora_da_lista")


# R2 do QA do #6 + ressalva 2 do Tester: nomes sensíveis reconhecidos de forma robusta, não só pelo nome exato.
# O nome é normalizado (sem acento, minúsculo, camelCase/espaço/hífen/ponto viram "_") e comparado por
# (a) trecho sensível em qualquer posição (prefixo/sufixo: Senha_Web, UserPassword, api_token...) e
# (b) tokens curtos só como palavra inteira (CID, CID10, Cod_CID, Cor, Renda, Secret, Pwd...), para não pegar
#     Cidade/Cidadao/Corpo/Secretaria.
_TRECHOS_PROIBIDOS = ("senha", "password", "passwd", "token", "segredo", "secretkey", "clientsecret", "apisecret", "userpwd", "hash_senha", "ctps",
                      "titulo_eleitor", "tituloeleitor", "reservista", "cnh", "salario", "deficien",
                      "religi", "etnia", "biometri", "remember")
_TOKENS_PROIBIDOS = re.compile(r"^(?:cid\d*|cor|raca|pin|titulo|cnh|renda|secret|secrets|pwd)$")


def normaliza_coluna(nome: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(nome))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)          # camelCase -> camel_Case
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return re.sub(r"_+", "_", s)


def coluna_proibida(nome: str, proibidas=frozenset()) -> bool:
    """True se a coluna é sensível pelo nome (padrão comum) ou consta da lista da política (comparação normalizada)."""
    n = normaliza_coluna(nome)
    compacto = n.replace("_", "")
    if n in {normaliza_coluna(p) for p in proibidas} or compacto in {normaliza_coluna(p).replace("_", "") for p in proibidas}:
        return True
    if any(t.replace("_", "") in compacto for t in _TRECHOS_PROIBIDOS):
        return True
    return any(_TOKENS_PROIBIDOS.match(tok) for tok in n.split("_"))


def filtra_colunas(linha: dict, permitidas, proibidas=frozenset()) -> dict:
    """Só colunas da lista permitida e nunca uma sensível: o que sobra é o que pode chegar à bronze."""
    perm = set(permitidas)
    return {k: v for k, v in linha.items() if k in perm and not coluna_proibida(k, proibidas)}


@dataclass(frozen=True)
class Politica:
    sistema: str
    base: str
    colunas: dict[str, tuple[str, ...]]          # tabela permitida -> colunas do SELECT
    apoio: frozenset[str] = field(default_factory=frozenset)  # lookups: autorizados, mas sem colunas definidas -> recusados no SELECT
    recusar: tuple[str, ...] = ()                 # padrões sempre recusados (regex, sem distinção de caixa)
    proibidas: frozenset[str] = field(default_factory=frozenset)
    ordem: dict[str, tuple[str, ...]] = field(default_factory=dict)  # tabela -> ORDER BY estável da amostra
    # TR1: recorte fixo por tabela, aplicado em TODA consulta (página, contagem, chave, nulos, assinatura, amostra):
    # tabela -> (coluna, função que devolve o mínimo inteiro). Ex.: Financeiro_Exercicio só dos exercícios recentes.
    filtros: dict[str, tuple[str, Callable[[], int]]] = field(default_factory=dict)

    def __post_init__(self):
        autoriza_base(self.base)  # A2: nenhuma política pode apontar para banco de descarte/Internet

    def filtro_fixo(self, tabela: str) -> str:
        """Condição SQL do recorte fixo (só inteiro, nunca texto vindo de fora) ou "" sem recorte."""
        nome = tabela.split(".")[-1]
        if nome not in self.filtros:
            return ""
        col, minimo = self.filtros[nome]
        if col not in self.colunas.get(nome, ()):
            raise TabelaRecusada(f"Recorte de {nome} fora das colunas da política.")
        v = minimo()
        if isinstance(v, date):  # data (ex.: últimos 12 meses): literal ISO gerado de um objeto date, nunca de texto
            return f"([{col}] >= '{v.isoformat()}')"
        return f"([{col}] >= {int(v)})"

    def ordem_amostra(self, tabela: str) -> tuple[str, ...]:
        nome = tabela.split(".")[-1]
        cols = self.ordem.get(nome, ())
        fora = [c for c in cols if c not in self.colunas.get(nome, ())]
        if fora:
            raise TabelaRecusada(f"Ordenação fora das colunas da política: {fora}")
        return cols

    def autoriza(self, tabela: str) -> None:
        partes = [p.strip("[]") for p in str(tabela).split(".")]
        if len(partes) >= 3:  # Base.schema.Tabela: o banco também passa pela A2
            autoriza_base(partes[-3])
        nome = partes[-1]
        for p in (*self.recusar, *_RECUSA_COMUM):  # PR-04: o padrão comum vale para TODA política
            if re.search(p, nome, re.IGNORECASE):
                raise TabelaRecusada(f"Tabela {nome} recusada pela política de {self.sistema}.", "tabela_padrao_recusado")
        if nome not in self.colunas and nome not in self.apoio:
            raise TabelaRecusada(f"Tabela {nome} fora da lista permitida de {self.sistema}.", "tabela_fora_da_lista")

    def colunas_select(self, tabela: str) -> tuple[str, ...]:
        self.autoriza(tabela)
        nome = tabela.split(".")[-1]
        cols = self.colunas.get(nome, ())
        if not cols:  # tabelas de apoio ainda sem lista de colunas: recusa explícita (ressalva do QA do #4)
            raise TabelaRecusada(f"Tabela {nome} de {self.sistema} sem colunas definidas na política.", "tabela_sem_colunas")
        ruins = {c for c in cols if coluna_proibida(c, self.proibidas)}
        if ruins:
            raise TabelaRecusada(f"Colunas proibidas na política: {sorted(ruins)}", "coluna_proibida")
        return cols


_RECUSA_COMUM = (r"UsuariosWeb$", r"^2APAGAR", r"APAGAR", r"bak", r"back_?up", r"^Copia", r"Antes", r"temp",
                 r"teste", r"_\d{6,}", r"\d{6,}[A-Za-z]*$", r"^EXCLUIDOS_", r"Duplicados", r"ffff",
                 # PR-04 (plano §10 R2): backups/cópias/descartes nomeados dentro dos próprios bancos
                 r"bkp", r"^AAA_NAO_UTILIZADO", r"Apagar_", r"_Anteriores$", r"Copia_", r"_Copia")

SAUDE = Politica(
    sistema="SAUDE", base="Saude",
    colunas={
        "pacientes": ("Matricula", "Nome", "NomeSocial", "Cpf", "Cartao_Sus", "Dt_Nasc", "Sexo", "Mae", "Pai",
                      "Cd_Rua", "Nr", "Cod_Bairro", "Cep", "Complemento", "Falecido", "DataObito",
                      "Normalizado", "Matricula_Correta", "Dt_Cad", "dt_ultimaatual"),
        "Pacientes_CNS": ("Matricula", "Cartao_Sus", "Data_Atribuicao", "Tipo_Cartao"),
        "ESUS_Cadastro_Domiciliar": ("Codigo_Cadastro", "UUID", "Nome_Logradouro", "Numero_Imovel", "Complemento",
                                     "Nome_Bairro", "CEP", "Nu_CNES_Unidade_Saude", "Nu_INE", "Data_Cadastro"),
        "ESUS_Cadastro_Domiciliar_Familia": ("Codigo_Domicilio_Familia", "Codigo_Cadastro_Domiciliar", "Matricula",
                                             "Cartao_Sus", "Data_Mudanca"),
        "ESUS_Paciente_Inconsistencia": ("Id", "Cartao_Sus", "Nome_Paciente", "Data_Nascimento", "Nome_Mae",
                                         "Data_Origem", "Status_Resolvido"),
        "Medicos": ("Codigo", "Nome", "CPF", "Cartao_SUS", "Tp_Conselho", "Nr_Conselho", "UF_Conselho", "Ativo",
                    "Dt_Inclusao", "Dt_UltimaAtual"),
        "ProfissionaisCNES": ("id", "Codigo", "DataInclusao", "CNES", "INE", "CBO", "Nome", "Ativo"),
        # Onda 3 (SA-F1): fatos de atendimento SEM conteúdo clínico (queixa, HMA, suspeita, exame, sinais vitais fora)
        "Consultas": ("Cod_Paciente", "Cons_Paciente", "Dt_Atend", "Cod_Ubs", "Codigo_Especialidade",
                      "CodigoGrupoAtendimento", "ESUSTipoAtendimento"),
        "Agendamento_Consulta": ("Cod_Ubs", "Cod_Prof", "Cod_Pac", "Dt_Cons", "Hr_Cons", "Cod_Espec",
                                 "DataHoraAgendamento", "Data_Hora_SIGS"),
        "Vacina": ("Matricula", "Cod_Vacina", "Sequencia", "Cod_Unidade", "Dt_Vacina", "DataGravacao"),
        "Unidades": ("Codigo", "Nome_Fantasia", "Tipo", "Inativo_Ativo", "Cd_Bai"),
        "Aux_Vacina": ("Cod_Vacina", "Vacina", "Dose"),
        "Aux_Especialidades": ("Codi_Espe", "Desc_Espe"),
    },
    filtros={"Consultas": ("Dt_Atend", lambda: date.today() - timedelta(days=365)),
             "Agendamento_Consulta": ("Dt_Cons", lambda: date.today() - timedelta(days=365)),
             "Vacina": ("Dt_Vacina", lambda: date.today() - timedelta(days=730))},
    apoio=frozenset({"Aux_Bairro", "Aux_Rua", "Aux_EnderecoTipoLogradouro", "Aux_Municipio", "Municipio",
                     "Aux_TpUnidade", "UnidadesESF", "UnidadesESFAgentes", "Aux_profissoes", "Medico_CBO",
                     "Aux_CBO_DePara"}),
    recusar=_RECUSA_COMUM,
    proibidas=frozenset({"Hipertenso", "Diabetico", "TeveAvc", "Acamado", "Cancer", "DoencaMental", "Tabagista",
                         "TemDoencaRespiratoria", "TemDoencaRespiratoriaDPOC", "TemDoencaRespiratoriaOutro",
                         "TemDoencaCardiaca", "TemDoencaCardiacaInsuficiencia", "TemDoencaCardiacaOutro",
                         "DeficienciaAuditiva", "DeficienciaIntelectual", "DeficienciaVisual", "DeficienciaFisica",
                         "DeficienciaOutros", "TemAlgumaDeficiencia", "Tipo_Sangue", "ObservacaoFraldas",
                         "LimiteFraldas", "SADAcompanhado", "Observacao", "Cor", "ID_ETNIA", "Visualiz_Senha",
                         "Queixa_principal", "HMA", "Suspeita", "Exame_Fisico", "Justif_Exames", "PA", "FC", "Peso",
                         "Altura", "Temperatura", "Glicosimetria", "FreqResp", "Glasgow", "MotivoExclusao",
                         "JustificativaExtra", "Lote", "LoteDescricao"}),
)

_CHAVE_SEP = ("COD_CEMITERIO", "QUADRA", "SEPULTURA", "GAVETA", "LADO", "SEQUENCIA")
_OBITO = _CHAVE_SEP + ("FALECIDO", "PAI", "MAE", "SEXO", "NATURAL", "EST_CIVIL", "IDADE", "DT_MORTE", "DT_SEPULTADO",
                       "Data_Inclusao", "Data_Ultima_Alteracao")
# Decisão do TL (#10): tabelas AAA_NAO_UTILIZADO_* fora de qualquer política (em prod faltavam colunas).
# A lista fica só para referência/demo sintética; a política recusa todas.
HISTORICAS_OBITO = ("AAA_NAO_UTILIZADO_OBITO01", "AAA_NAO_UTILIZADO_OBITO02",
                    "AAA_NAO_UTILIZADO_OBITO01_Sepulta_Anterior1982", "AAA_NAO_UTILIZADO_ObitosSAUDADE",
                    "AAA_NAO_UTILIZADO_ObitosSTOAGOSTINHO", "AAA_NAO_UTILIZADO_Obitos2018")

CEMITERIO = Politica(
    sistema="CEMITERIO", base="Cemiterio",
    colunas={
        "Obitos": _OBITO,
        "Titulares": ("Cod_Cemiterio", "Quadra", "Sepultura", "Sequencia", "Titular", "CPF", "Endereco", "Ativo", "Inclusao"),
        "Pessoas_Autorizadas": ("Cod_Cemiterio", "Quadra", "Sepultura", "Sequencia", "Nome", "CPF", "Parentesco", "Inclusao"),
        "Leilao": ("Cod_Cemiterio", "Quadra", "Sepultura", "DataVenda", "Nome", "CPF", "DataLiberacao"),
        "CAD_CEMITE": ("COD_CEMITERIO", "QUADRA", "SEPULTURA", "TITULAR", "TIPO_DOC", "DOCUMENTO", "ENDERECO", "FALECIDO",
                       "DT_MORTE"),
        "USUARIO_NOVO": ("COD_CEMITERIO", "QUADRA", "SEPULTURA", "SEQUENCIA", "USUARIO", "PARENTESCO", "TIPO_DOC",
                         "DOCUMENTO"),
        "Ocorrencia_Sepultura": ("Cod_Cemiterio", "Quadra", "Sepultura", "Seq_Ocorrencia", "Dt_Inclusao"),
        "Cemiterio": ("codcemite", "nome"),
    },
    apoio=frozenset({"AuxQuadra", "AuxSepultura", "Aux_Quadra", "Aux_EstCivil", "ALTERACAO"}),
    recusar=(r"^Novo_Cemiterio", r"CONGELADO", r"VELHO", r"Antigo", r"^AAA_NAO_UTILIZADO",
             r"Senha", r"^USUARIO$", r"USUARIODNOVO", r"bak", r"teste"),
    proibidas=frozenset({"COR", "CAUSA_MORTIS", "CAUSA", "CODIGO_OMS", "ASS_MEDICA", "MEDICO_OBITO", "CRM_MEDICO",
                         "AAA_NAO_UTILIZADO_Senha", "Usuario_Sistema", "CodUsu", "RG", "Fone", "Email"}),
)

# Munícipe (aceite M1-M8): fonte única ISS.dbo.Municipes (a Geral é espelho contido no ISS e não é lida).
# Colunas confirmadas na triagem (mapeamentos/triagem-bancos.md); CTPS, Título, Reservista, CNH e senhas nunca entram.
# Documento_SoNumeros varchar(14): só dígitos, mantido pelo ISS; serve para conferir a normalização (M2)
# e, com Tipo_Pessoa, ordena a amostra pelo índice (Tipo_Pessoa, Documento_SoNumeros) - decisão do TL no #6.
COLUNAS_MUNICIPES = ("Tipo_Pessoa", "CPF", "Nome", "Nome_Mae", "Data_Nascimento", "Pis_Pasep", "Data_Alteracao",
                     "Documento_SoNumeros")
ORDEM_MUNICIPES = ("Tipo_Pessoa", "Documento_SoNumeros")

ISS = Politica(
    sistema="ISS", base="ISS",
    colunas={"Municipes": COLUNAS_MUNICIPES},
    ordem={"Municipes": ORDEM_MUNICIPES},
    recusar=(r"Senha", r"Antigo") + _RECUSA_COMUM,
    proibidas=frozenset({"Senha", "CTPS", "Titulo_Eleitor", "Reservista", "CNH"}),
)

# ---- Tributário (onda 2, TR1–TR8): só fatos (imóvel, possuidor, lançamento, dívida); nunca cria cidadão ----
# Colunas escolhidas no dicionário (schema.md) — só o que a ficha e os painéis usam; nada de observação livre.
IPTU = Politica(
    sistema="IPTU", base="IPTU",
    colunas={
        "Cadastro": ("CODI", "Insc_Imob", "NR_CASE", "COMPLE", "Cd_Bai", "Quadra", "Lote", "STER", "SITU",
                     "Iptu_Terr_Liqu", "Iptu_Cons_Liqu", "Data_Alte"),
        "Cadastro_Possuidor": ("CODI", "CPF_CNPJ", "Data_Inclusao", "Data_Baixa", "Tipo_Posse"),
    },
    recusar=(r"Integracao", r"Carne", r"Nova_Planta", r"Excluidos", r"Alterado", r"Log") + _RECUSA_COMUM,
    proibidas=frozenset({"NOME", "Email", "RG", "Celular", "Telefone", "OBS", "Observacao"}),
)

DIVIDA = Politica(
    sistema="DIVIDA", base="Divida",
    colunas={"Divida": ("CODI", "SEQU", "ANO", "Nr_Carne", "Cod_Nat", "DT_INSCR", "DT_VENC", "VR_ORIGEM", "VR_TOTA_DIVI",
                        "VR_PAGO_TOTAL", "STATUS", "SITUACAO", "DT_PAGAMENTO", "Data_Extincao", "QTDE_PROTESTO",
                        "NR_PROC", "DT_CALCULO")},
    recusar=(r"Planilha", r"Finalizados", r"Teste", r"SMS") + _RECUSA_COMUM,
    proibidas=frozenset({"OBS", "Info_Susp", "Moti_Extincao"}),
)


def _exercicio_minimo() -> int:
    from datetime import date

    from app.core.config import get_settings
    v = get_settings().financeiro_exercicio_min
    return v if v else date.today().year - 1  # padrão: exercício atual e o anterior


FINANCEIRO = Politica(
    sistema="FINANCEIRO", base="Financeiro",
    colunas={"Financeiro_Exercicio": ("Exercicio", "Tributo", "Nr_Carne", "Sequencia", "Referencia", "Renovacao",
                                      "Parcela", "Duplicidade", "Contribuinte", "Receita", "Data_Vencimento", "Valor",
                                      "Data_Pagamento", "Valor_Pago", "Status", "Data_Ultima_Alteracao_Registro")},
    filtros={"Financeiro_Exercicio": ("Exercicio", _exercicio_minimo)},
    recusar=(r"Excluid", r"Alterados", r"Integracao", r"MPU_", r"Log_") + _RECUSA_COMUM,
    proibidas=frozenset({"Documento", "Arquivo", "Linha"}),
)

# API SIL (REST, não SQL): a política só fixa as colunas gravadas na bronze (o conector já achata o JSON nelas).
# E-mail, telefone e endereço de entrega da empresa ficam fora.
SIL = Politica(
    sistema="SIL", base="API SIL",
    colunas={"Empresas": ("Ccm", "CNPJCPF", "Nome", "PossuiDebito", "Endereco", "Bairro", "CEP", "Complemento",
                          "AreaOcupada", "AreaUtilizada")},
    proibidas=frozenset({"Email", "Telefone", "LocalEntrega"}),
)

POLITICAS: dict[str, Politica] = {"SAUDE": SAUDE, "CEMITERIO": CEMITERIO, "ISS": ISS, "IPTU": IPTU,
                                  "DIVIDA": DIVIDA, "FINANCEIRO": FINANCEIRO, "SIL": SIL}


def classifica_listagem(pol: Politica, nomes) -> tuple[list[str], list[tuple[str, str]]]:
    """A1: o que o conector lista na origem é separado em (permitidas, [(recusada, codigo)]). Quem chama audita as recusas."""
    ok, recusadas = [], []
    for n in nomes:
        try:
            pol.autoriza(n)
            ok.append(n)
        except TabelaRecusada as exc:
            recusadas.append((n, exc.codigo))
    return ok, recusadas


def audita_recusas(db, usuario: str | None, sistema: str, recusadas, commit: bool = True) -> int:
    """Uma linha de auditoria `tabela_recusada` por tabela recusada (só nome e código fixo, nenhum dado de linha)."""
    from app.core.audit import audita
    for nome, codigo in recusadas:
        audita(db, usuario, "tabela_recusada", f"{sistema}.{nome}", {"codigo": codigo}, commit=False)
    if commit:
        db.commit()
    return len(recusadas)


def politica(sistema: str) -> Politica | None:
    return POLITICAS.get(sistema)
