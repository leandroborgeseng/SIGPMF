"""Mapeamento PLUGÁVEL dos endpoints de consulta da SIL (o escopo final vem do Analista).

Cada endpoint diz o caminho (GET), o parâmetro da chave, o tipo da chave e a tabela da bronze onde a resposta bruta
cai. Registrar um endpoint fora da allowlist de GET (cliente.GET_PERMITIDOS) falha na hora."""
from dataclasses import dataclass

from app.connectors.sil.cliente import OperacaoProibida, checa_operacao


@dataclass(frozen=True)
class EndpointSil:
    nome: str
    caminho: str
    parametro: str
    tipo_chave: str          # inscricao | documento | ccm | atividade
    tabela_bronze: str
    descricao: str = ""
    paginado: bool = False       # SIL11: listagem por Sequencia (consultarlote)
    campo_id: str | None = None  # campo do item usado como ID da paginação (a confirmar na 1ª amostra)
    fixos: tuple[tuple[str, str], ...] = ()  # parâmetros fixos (ex.: Status=Ativo)


ENDPOINTS: dict[str, EndpointSil] = {}


def registrar(e: EndpointSil) -> EndpointSil:
    checa_operacao("GET", e.caminho)  # OperacaoProibida se não for GET permitido
    if not e.parametro.isidentifier():
        raise OperacaoProibida(f"parâmetro inválido para {e.nome}")
    ENDPOINTS[e.nome] = e
    return e


def endpoint(nome: str) -> EndpointSil:
    try:
        return ENDPOINTS[nome]
    except KeyError:
        raise LookupError(f"endpoint SIL desconhecido: {nome}") from None


# Mapeamento inicial pela doc (aceite-conector-sil.md); ajustável quando o Analista fechar o escopo.
registrar(EndpointSil("imoveis", "/franca/imoveis", "InscrFisico", "inscricao", "imoveis",
                      "1 imóvel por inscrição; 404 = não localizado"))
registrar(EndpointSil("empresas", "/franca/empresas", "Documento", "documento", "empresas",
                      "empresa por CCM ou CNPJ/CPF"))
registrar(EndpointSil("atividade", "/franca/atividade", "codatividade", "atividade", "atividade",
                      "empresas ativas das atividades com filtro fixo"))
# SIL11: listagem de empresas por Sequencia (maior ID recebido = próxima Sequencia). Na doc o item vem em
# data.empresas[].empresa e o único identificador numérico é Ccm: campo_id = "Ccm" até a 1ª amostra confirmar.
registrar(EndpointSil("empresas_lote", "/franca/empresa/consultarlote", "Sequencia", "sequencia", "empresas",
                      "listagem paginada de empresas ativas (um lote por padrão)", paginado=True, campo_id="Ccm",
                      fixos=(("Status", "Ativo"),)))
