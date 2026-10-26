"""Origem simulada com a fixture sintética. Mesma interface de leitura do conector SQL Server
(lê no máximo `limite` registros, ordenados como na origem) e registra as consultas emitidas."""
from app.ingestion.fixture_sintetica import SEED, gerar


class OrigemMock:
    def __init__(self, seed: int = SEED):
        self._dados = {k: [ln["dados"] for ln in v] for k, v in gerar(seed).items()}
        self.consultas: list[str] = []

    def tabelas(self) -> list[tuple[str, str]]:
        return list(self._dados)

    def ler(self, sistema: str, tabela: str, limite: int | None) -> list[dict]:
        self.consultas.append(f"SELECT TOP ({limite}) * FROM [{tabela}]" if limite else f"SELECT * FROM [{tabela}]")
        linhas = self._dados[(sistema, tabela)]
        return [dict(x) for x in (linhas[:limite] if limite else linhas)]
