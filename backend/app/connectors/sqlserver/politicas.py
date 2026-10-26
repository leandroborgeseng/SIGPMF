"""Políticas por sistema de origem (aceites S1/S3 da Saúde e CM1/CM2 do Cemitério).

- Só tabelas da lista permitida são lidas; o resto é recusado (e a recusa é auditada por quem chama).
- Para cada tabela, só as colunas listadas entram no SELECT. Colunas sensíveis nunca entram.
"""
import re
from dataclasses import dataclass, field


class TabelaRecusada(ValueError):
    pass


@dataclass(frozen=True)
class Politica:
    sistema: str
    base: str
    colunas: dict[str, tuple[str, ...]]          # tabela permitida -> colunas do SELECT
    apoio: frozenset[str] = field(default_factory=frozenset)  # lookups permitidos (SELECT * não; colunas a definir)
    recusar: tuple[str, ...] = ()                 # padrões sempre recusados (regex, sem distinção de caixa)
    proibidas: frozenset[str] = field(default_factory=frozenset)

    def autoriza(self, tabela: str) -> None:
        nome = tabela.split(".")[-1]
        for p in self.recusar:
            if re.search(p, nome, re.IGNORECASE):
                raise TabelaRecusada(f"Tabela {nome} recusada pela política de {self.sistema}.")
        if nome not in self.colunas and nome not in self.apoio:
            raise TabelaRecusada(f"Tabela {nome} fora da lista permitida de {self.sistema}.")

    def colunas_select(self, tabela: str) -> tuple[str, ...]:
        self.autoriza(tabela)
        cols = self.colunas.get(tabela.split(".")[-1], ())
        ruins = {c for c in cols if c.lower() in {p.lower() for p in self.proibidas}}
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
HISTORICAS_OBITO = ("AAA_NAO_UTILIZADO_OBITO01", "AAA_NAO_UTILIZADO_OBITO02",
                    "AAA_NAO_UTILIZADO_OBITO01_Sepulta_Anterior1982", "AAA_NAO_UTILIZADO_ObitosSAUDADE",
                    "AAA_NAO_UTILIZADO_ObitosSTOAGOSTINHO", "AAA_NAO_UTILIZADO_Obitos2018")

CEMITERIO = Politica(
    sistema="CEMITERIO", base="Cemiterio",
    colunas={
        "Obitos": _OBITO,
        **{h: _OBITO for h in HISTORICAS_OBITO},
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
    recusar=(r"^Novo_Cemiterio", r"CONGELADO", r"VELHO", r"Antigo", r"^AAA_NAO_UTILIZADO_(?!OBITO0[12]$|OBITO01_Sepulta_Anterior1982$|Obitos(SAUDADE|STOAGOSTINHO|2018)$)",
             r"Senha", r"^USUARIO$", r"USUARIODNOVO", r"bak", r"teste"),
    proibidas=frozenset({"COR", "CAUSA_MORTIS", "CAUSA", "CODIGO_OMS", "ASS_MEDICA", "MEDICO_OBITO", "CRM_MEDICO",
                         "AAA_NAO_UTILIZADO_Senha", "Usuario_Sistema", "CodUsu", "RG", "Fone", "Email"}),
)

POLITICAS: dict[str, Politica] = {"SAUDE": SAUDE, "CEMITERIO": CEMITERIO}


def politica(sistema: str) -> Politica | None:
    return POLITICAS.get(sistema)
