"""Políticas por sistema de origem (aceites S1/S3 da Saúde e CM1/CM2 do Cemitério).

- Só tabelas da lista permitida são lidas; o resto é recusado (e a recusa é auditada por quem chama).
- Para cada tabela, só as colunas listadas entram no SELECT. Colunas sensíveis nunca entram.
"""
import re
from dataclasses import dataclass, field


class TabelaRecusada(ValueError):
    pass


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

    def ordem_amostra(self, tabela: str) -> tuple[str, ...]:
        nome = tabela.split(".")[-1]
        cols = self.ordem.get(nome, ())
        fora = [c for c in cols if c not in self.colunas.get(nome, ())]
        if fora:
            raise TabelaRecusada(f"Ordenação fora das colunas da política: {fora}")
        return cols

    def autoriza(self, tabela: str) -> None:
        nome = tabela.split(".")[-1]
        for p in self.recusar:
            if re.search(p, nome, re.IGNORECASE):
                raise TabelaRecusada(f"Tabela {nome} recusada pela política de {self.sistema}.")
        if nome not in self.colunas and nome not in self.apoio:
            raise TabelaRecusada(f"Tabela {nome} fora da lista permitida de {self.sistema}.")

    def colunas_select(self, tabela: str) -> tuple[str, ...]:
        self.autoriza(tabela)
        nome = tabela.split(".")[-1]
        cols = self.colunas.get(nome, ())
        if not cols:  # tabelas de apoio ainda sem lista de colunas: recusa explícita (ressalva do QA do #4)
            raise TabelaRecusada(f"Tabela {nome} de {self.sistema} sem colunas definidas na política.")
        ruins = {c for c in cols if coluna_proibida(c, self.proibidas)}
        if ruins:
            raise TabelaRecusada(f"Colunas proibidas na política: {sorted(ruins)}")
        return cols


_RECUSA_COMUM = (r"UsuariosWeb$", r"^2APAGAR", r"APAGAR", r"bak", r"back_?up", r"^Copia", r"Antes", r"temp",
                 r"teste", r"_\d{6,}", r"\d{6,}[A-Za-z]*$", r"^EXCLUIDOS_", r"Duplicados", r"ffff")

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
    },
    apoio=frozenset({"Aux_Bairro", "Aux_Rua", "Aux_EnderecoTipoLogradouro", "Aux_Municipio", "Municipio", "Unidades",
                     "Aux_TpUnidade", "UnidadesESF", "UnidadesESFAgentes", "Aux_profissoes", "Medico_CBO",
                     "Aux_CBO_DePara"}),
    recusar=_RECUSA_COMUM,
    proibidas=frozenset({"Hipertenso", "Diabetico", "TeveAvc", "Acamado", "Cancer", "DoencaMental", "Tabagista",
                         "TemDoencaRespiratoria", "TemDoencaRespiratoriaDPOC", "TemDoencaRespiratoriaOutro",
                         "TemDoencaCardiaca", "TemDoencaCardiacaInsuficiencia", "TemDoencaCardiacaOutro",
                         "DeficienciaAuditiva", "DeficienciaIntelectual", "DeficienciaVisual", "DeficienciaFisica",
                         "DeficienciaOutros", "TemAlgumaDeficiencia", "Tipo_Sangue", "ObservacaoFraldas",
                         "LimiteFraldas", "SADAcompanhado", "Observacao", "Cor", "ID_ETNIA", "Visualiz_Senha"}),
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

POLITICAS: dict[str, Politica] = {"SAUDE": SAUDE, "CEMITERIO": CEMITERIO, "ISS": ISS}


def politica(sistema: str) -> Politica | None:
    return POLITICAS.get(sistema)
