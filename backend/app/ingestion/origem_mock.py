"""Origens simuladas com fixtures sintéticas. Mesma interface de leitura do conector SQL Server:
lê no máximo `limite` linhas, só as colunas permitidas pela política do sistema, e registra as consultas."""
from app.ingestion import fixture_saude_cemiterio
from app.ingestion.fixture_sintetica import SEED, gerar

CONJUNTOS = ("base", "saude", "cemiterio", "saude+cemiterio")


class OrigemMock:
    def __init__(self, seed: int = SEED, conjunto: str = "base"):
        if conjunto not in CONJUNTOS:
            raise ValueError(f"Conjunto inválido: {conjunto}")
        dados: dict = {}
        if conjunto == "base":
            dados.update({k: [ln["dados"] for ln in v] for k, v in gerar(seed).items()})
        else:
            fx = fixture_saude_cemiterio.gerar()
            for parte in conjunto.split("+"):
                dados.update(fx[parte])
        self._dados = dados
        self.consultas: list[str] = []

    def tabelas(self) -> list[tuple[str, str]]:
        return list(self._dados)

    def ler(self, sistema: str, tabela: str, limite: int | None, colunas: tuple[str, ...] | None = None) -> list[dict]:
        cols = ", ".join(f"[{c}]" for c in colunas) if colunas else "*"
        self.consultas.append(f"SELECT TOP ({limite}) {cols} FROM [{tabela}]" if limite else f"SELECT {cols} FROM [{tabela}]")
        linhas = self._dados[(sistema, tabela)]
        linhas = linhas[:limite] if limite else linhas
        if colunas:
            return [{c: x.get(c) for c in colunas} for x in linhas]
        return [dict(x) for x in linhas]
