"""Lista FECHADA (allowlist) de tabelas e colunas que o explorador lê. Nada fora daqui vira SQL (EX3).

Prata/ouro: tabelas e colunas vêm dos modelos SQLAlchemy (nomes fixos no código). Bronze: as "tabelas" são valores
de bronze.registros.tabela e as "colunas" são chaves do JSON `dados`; ambos só entram na consulta como PARÂMETRO
(bind), nunca como identificador SQL, e precisam existir nos dados do próprio conector.
"""
from dataclasses import dataclass, field

from sqlalchemy import Table

from app.models.camadas import (
    Cidadao,
    CidadaoSistemas,
    CorrelacaoPendente,
    DadoGold,
    Endereco,
    Identificador,
    Inconsistencia,
    Municipe,
    MunicipePendencia,
    MunicipeVersao,
    Vinculo,
)

CAMADAS = ("bronze", "prata", "ouro")


@dataclass(frozen=True)
class Spec:
    nome: str
    tabela: Table
    id_col: str
    # como filtrar pelo conector: coluna "sistema", via vinculos (cidadao_id), json array, ou só um sistema fixo
    filtro: str
    sistema_fixo: str | None = None
    rotulo_col: str | None = None   # tabela chave/valor: a sensibilidade de valor_col depende do rótulo
    valor_col: str | None = None
    cidadao_col: str | None = None  # EX5: permite abrir a linhagem
    ocultas: tuple[str, ...] = field(default=())  # colunas nunca exibidas (ex.: hash)

    @property
    def colunas(self) -> list[str]:
        return [c.name for c in self.tabela.columns if c.name not in self.ocultas]

    def tipo(self, col: str) -> str:
        try:
            return str(self.tabela.columns[col].type).lower()
        except Exception:  # noqa: BLE001
            return "?"


def _t(m) -> Table:
    return m.__table__


PRATA: dict[str, Spec] = {s.nome: s for s in (
    Spec("vinculos", _t(Vinculo), "id", "sistema", cidadao_col="cidadao_id"),
    Spec("cidadaos", _t(Cidadao), "cidadao_id", "vinculos", cidadao_col="cidadao_id"),
    Spec("identificadores", _t(Identificador), "id", "vinculos", rotulo_col="tipo", valor_col="valor",
         cidadao_col="cidadao_id"),
    Spec("enderecos", _t(Endereco), "id", "sistema", cidadao_col="cidadao_id"),
    Spec("correlacoes_pendentes", _t(CorrelacaoPendente), "id", "sistema"),
    Spec("inconsistencias", _t(Inconsistencia), "id", "sistema", cidadao_col="cidadao_id"),
    Spec("municipes", _t(Municipe), "documento", "fixo", sistema_fixo="ISS", cidadao_col="cidadao_id",
         ocultas=("hash",)),
    Spec("municipes_versoes", _t(MunicipeVersao), "id", "fixo", sistema_fixo="ISS", ocultas=("hash",)),
    Spec("municipes_pendencias", _t(MunicipePendencia), "chave", "fixo", sistema_fixo="ISS"),
)}

OURO: dict[str, Spec] = {s.nome: s for s in (
    Spec("cidadao_dados", _t(DadoGold), "id", "sistema", rotulo_col="campo", valor_col="valor", cidadao_col="cidadao_id"),
    Spec("cidadao_sistemas", _t(CidadaoSistemas), "cidadao_id", "json_array", cidadao_col="cidadao_id"),
)}

POR_CAMADA = {"prata": PRATA, "ouro": OURO}

# Colunas de bronze.registros exibidas ao lado das chaves do JSON
BRONZE_META = ("id", "id_origem", "extraido_em", "atualizado_origem_em")
BRONZE_AMOSTRA_CHAVES = 2000  # linhas lidas para descobrir as chaves do JSON
MAX_LINHAS = 50
# UX/perf: acima disso o perfil (preenchido %, distintos) é calculado numa amostra limitada e marcado como estimativa
PERFIL_AMOSTRA = 10000

# P1 (ressalva 1 do Tester no #16): na bronze não há catálogo de colunas. Só as colunas desta lista FECHADA, por
# conector e tabela, aparecem em claro para o admin comum; qualquer outra chave do JSON (inclusive nomes fora do
# padrão, como NU_INSCR ou Ident) vem mascarada e só o admin_lgpd revela. Subconjunto não pessoal das colunas da
# política de leitura de cada conector (conferido em teste). Decisão do TL: Matricula (Saúde), UUID e
# Codigo_Cadastro/Codigo_Cadastro_Domiciliar/Codigo_Domicilio_Familia (e-SUS, risco de junção) identificam a pessoa/domicílio e ficam FORA (mascarados para o admin comum).
BRONZE_COLUNAS_LIVRES: dict[str, dict[str, frozenset[str]]] = {
    "SAUDE": {
        "pacientes": frozenset({"Sexo", "Normalizado", "Dt_Cad", "dt_ultimaatual"}),
        "Pacientes_CNS": frozenset({"Data_Atribuicao", "Tipo_Cartao"}),
        "ESUS_Cadastro_Domiciliar": frozenset({"Nu_CNES_Unidade_Saude", "Nu_INE", "Data_Cadastro"}),
        "ESUS_Cadastro_Domiciliar_Familia": frozenset({"Data_Mudanca"}),
        "ESUS_Paciente_Inconsistencia": frozenset({"Id", "Data_Origem", "Status_Resolvido"}),
        "Medicos": frozenset({"Codigo", "Tp_Conselho", "Nr_Conselho", "UF_Conselho", "Ativo", "Dt_Inclusao", "Dt_UltimaAtual"}),
        "ProfissionaisCNES": frozenset({"id", "Codigo", "DataInclusao", "CNES", "INE", "CBO", "Ativo"}),
    },
    "CEMITERIO": {
        "Obitos": frozenset({"COD_CEMITERIO", "QUADRA", "SEPULTURA", "GAVETA", "LADO", "SEQUENCIA", "DT_SEPULTADO",
                             "Data_Inclusao", "Data_Ultima_Alteracao"}),
        "Titulares": frozenset({"Cod_Cemiterio", "Quadra", "Sepultura", "Sequencia", "Ativo", "Inclusao"}),
        "Pessoas_Autorizadas": frozenset({"Cod_Cemiterio", "Quadra", "Sepultura", "Sequencia", "Parentesco", "Inclusao"}),
        "Leilao": frozenset({"Cod_Cemiterio", "Quadra", "Sepultura", "DataVenda", "DataLiberacao"}),
        "CAD_CEMITE": frozenset({"COD_CEMITERIO", "QUADRA", "SEPULTURA"}),
        "USUARIO_NOVO": frozenset({"COD_CEMITERIO", "QUADRA", "SEPULTURA", "SEQUENCIA", "PARENTESCO"}),
        "Ocorrencia_Sepultura": frozenset({"Cod_Cemiterio", "Quadra", "Sepultura", "Seq_Ocorrencia", "Dt_Inclusao"}),
        "Cemiterio": frozenset({"codcemite"}),
    },
    "ISS": {"Municipes": frozenset({"Tipo_Pessoa", "Data_Alteracao"})},
}


def bronze_livres(conector: str, tabela: str) -> frozenset[str]:
    return BRONZE_COLUNAS_LIVRES.get(conector, {}).get(tabela, frozenset())
