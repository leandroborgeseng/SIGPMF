"""Fixture 100% SINTÉTICA (casos FX01 a FX11 do plano do QA), gerada com semente fixa.

Nenhum CPF é escrito no repositório: todos são gerados em tempo de execução (módulo 11).
Cada linha carrega uma etiqueta de oráculo (fora dos dados da origem) que permite calcular as
contagens esperadas sem reaproveitar o código de correlação.
"""
import random
from collections import OrderedDict

from app.identity.cpf import digitos_verificadores

SEED = 20261025
CEP = "14400-000"
BAIRRO = "Bairro Sintético"
SAUDE = ("DEMO_SAUDE", "prontuario")
TRIB = ("DEMO_CEMITERIO", "sepultamento")
_PRENOMES = ["Lucas", "Marina", "Otavio", "Paula", "Rafael", "Sofia", "Tiago", "Vera", "Wagner", "Yara"]
_SOBRENOMES = ["Amostra", "Simulado", "Ficcao", "Prototipo", "Ensaio", "Rascunho", "Maquete", "Esboco"]


class _Gerador:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.usados: set[str] = set()

    def cpf(self) -> str:
        while True:
            base = f"{self.rng.randrange(10**8, 10**9):09d}"
            if base == base[0] * 9:
                continue
            c = base + digitos_verificadores(base)
            if c not in self.usados:
                self.usados.add(c)
                return c

    def nasc(self) -> str:
        return f"{self.rng.randint(1940, 2015)}-{self.rng.randint(1, 12):02d}-{self.rng.randint(1, 28):02d}"


def _mascara_formatada(c: str) -> str:
    return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"


def _dv_errado(c: str) -> str:
    return c[:10] + str((int(c[10]) + 1) % 10)


def gerar(seed: int = SEED) -> dict[tuple[str, str], list[dict]]:
    """Retorna {(sistema, tabela): [{"dados": {...}, "oraculo": {...}}]} na ordem de leitura da origem."""
    g = _Gerador(seed)
    s: list[dict] = []
    t: list[dict] = []
    ns = iter(range(1, 10**6))
    nt = iter(range(1, 10**6))

    def pac(nome, nasc, cpf, mae, end, data, oraculo, rid=None):
        rid = rid or f"S{next(ns):05d}"
        s.append({"dados": {"id": rid, "nome": nome, "nascimento": nasc, "cpf": cpf, "nome_mae": mae,
                            "endereco": f"{end}, {BAIRRO}, {CEP}", "atualizado_em": data}, "oraculo": oraculo})

    def contrib(nome, nasc, cpf, mae, end, data, oraculo):
        t.append({"dados": {"cod": f"T{next(nt):05d}", "nome": nome, "dt_nasc": nasc, "cpf_cnpj": cpf, "mae": mae,
                            "logradouro": f"{end}, {BAIRRO}, {CEP}", "dt_alteracao": data}, "oraculo": oraculo})

    D1, D2 = "2026-09-01T10:00:00-03:00", "2026-10-01T10:00:00-03:00"
    # FX01 mesmo CPF sem/com máscara
    ana = g.cpf()
    pac("Ana Teste Silva", "1980-01-15", ana, "Mae Teste Silva", "Rua Teste 10", D1, {"fx": "FX01", "pessoa": "FX01", "cria": True})
    contrib("Ana Teste Silva", "1980-01-15", _mascara_formatada(ana), "Mae Teste Silva", "Rua Teste 10", D2, {"fx": "FX01", "pessoa": "FX01", "cria": True})
    # FX02 dois endereços, o de 2026-03-05 é o mais recente
    br = g.cpf()
    pac("Bruno Exemplo Souza", "1975-06-02", br, "Mae Exemplo Souza", "Rua Teste 1", "2025-01-10T09:00:00-03:00", {"fx": "FX02", "pessoa": "FX02", "cria": True})
    contrib("Bruno Exemplo Souza", "1975-06-02", br, "Mae Exemplo Souza", "Rua Teste 2", "2026-03-05T09:00:00-03:00", {"fx": "FX02", "pessoa": "FX02", "cria": True})
    # FX03 homônimas, nascimentos diferentes, sem CPF -> score < 0,85 -> novos cidadao_id (v3)
    for nasc in ("1990-11-30", "1992-04-17"):
        pac("Carla Ficticia Lima", nasc, None, "Mae Ficticia Lima", "Rua Teste 3", D1, {"fx": "FX03", "novo_sem_cpf": True})
    # FX04 gêmeos: mesmo nascimento e mãe, CPFs distintos
    gemeos = [("Diego Teste Rocha", g.cpf()), ("Davi Teste Rocha", g.cpf())]
    for nome, c in gemeos:
        pac(nome, "2005-03-21", c, "Mae Teste Rocha", "Rua Teste 4", D1, {"fx": "FX04", "pessoa": nome, "cria": True})
    # FX05 mesmo nome e nascimento, mães diferentes, sem CPF -> score < 0,85 -> novos cidadao_id (v3)
    for mae in ("Mae Um Costa", "Mae Dois Costa"):
        pac("Elisa Modelo Costa", "1988-08-08", None, mae, "Rua Teste 5", D1, {"fx": "FX05", "novo_sem_cpf": True})
    # FX06 sem CPF na saúde, com CPF nos tributos -> score 0,85-0,99 -> revisão (não associa)
    pac("Fábio Teste Nunes", "1966-12-12", None, "Mae Teste Nunes", "Rua Teste 6", D1, {"fx": "FX06", "pendente": "revisao"})
    # FX07 DV inválido; FX08 dígitos repetidos -> inconsistência; sem CPF válido e sem par -> novo cidadao_id (v3)
    pac("Gabi Exemplo Reis", "2012-09-09", _dv_errado(g.cpf()), "Mae Exemplo Reis", "Rua Teste 7", D1, {"fx": "FX07", "novo_sem_cpf": True, "inconsistencia": "cpf_invalido"})
    pac("Hugo Teste Alves", "1999-02-02", "1" * 11, "Mae Teste Alves", "Rua Teste 8", D1, {"fx": "FX08", "novo_sem_cpf": True, "inconsistencia": "cpf_invalido"})
    # FX09 duplicata exata (mesmo id de origem)
    iris = g.cpf()
    rid = f"S{next(ns):05d}"
    for _ in range(2):
        pac("Íris Ficticia Melo", "1970-07-07", iris, "Mae Ficticia Melo", "Rua Teste 9", D1, {"fx": "FX09", "pessoa": "FX09", "cria": True}, rid=rid)
    # FX10 CPF do FX01 com nascimento divergente -> ambíguo, pendente
    pac("João Modelo Dias", "1981-05-05", ana, "Mae Modelo Dias", "Rua Teste 11", D1, {"fx": "FX10", "pendente": "ambiguo", "inconsistencia": "cpf_nascimento_divergente"})
    # FX06 lado tributos (com CPF)
    contrib("Fábio Teste Nunes", "1966-12-12", g.cpf(), "Mae Teste Nunes", "Rua Teste 6", D2, {"fx": "FX06", "pessoa": "FX06", "cria": True})
    for nome, c in gemeos:
        contrib(nome, "2005-03-21", c, "Mae Teste Rocha", "Rua Teste 4", D2, {"fx": "FX04", "pessoa": nome, "cria": True})
    # FX11 500 pessoas válidas; metade também nos tributos
    for i in range(500):
        nome = f"{g.rng.choice(_PRENOMES)} {g.rng.choice(_SOBRENOMES)} {g.rng.choice(_SOBRENOMES)} {i:03d}"
        c, nasc, n = g.cpf(), g.nasc(), (i % 90) + 12
        pac(nome, nasc, c, f"Mae {nome}", f"Rua Teste {n}", D1, {"fx": "FX11", "pessoa": f"FX11-{i}", "cria": True})
        if i < 250:
            contrib(nome, nasc, c, f"Mae {nome}", f"Rua Teste {n}", D2, {"fx": "FX11", "pessoa": f"FX11-{i}", "cria": True})
    return OrderedDict([(SAUDE, s), (TRIB, t)])


def contagens_esperadas(fixture: dict[tuple[str, str], list[dict]], limite: int | None) -> dict[str, int]:
    """Oráculo independente: deriva as contagens das etiquetas da fixture."""
    lidos = {k: (v[:limite] if limite else v) for k, v in fixture.items()}
    bronze = 0
    criados: set[str] = set()
    sem_cpf = 0
    vinc = pend = inc = 0
    pessoas_com_vinculo: dict[str, set[str]] = {}
    for (sis, tab), linhas in lidos.items():
        vistos = set()
        for ln in linhas:
            chave = tuple(sorted((k, str(v)) for k, v in ln["dados"].items()))
            if chave in vistos:
                continue
            vistos.add(chave)
            bronze += 1
            o = ln["oraculo"]
            if o.get("cria"):
                criados.add(o["pessoa"])
    for (sis, tab), linhas in lidos.items():
        ids = set()
        for ln in linhas:
            rid = ln["dados"].get("id") or ln["dados"].get("cod")
            if rid in ids:
                continue
            ids.add(rid)
            o = ln["oraculo"]
            if "inconsistencia" in o:
                inc += 1
            if o.get("cria"):
                vinc += 1
                pessoas_com_vinculo.setdefault(o["pessoa"], set()).add(sis)
            elif o.get("novo_sem_cpf"):
                vinc += 1
                sem_cpf += 1
            else:
                pend += 1
    return {"bronze_registros": bronze, "silver_cidadaos": len(criados) + sem_cpf,
            "silver_identificadores": len(criados), "silver_vinculos": vinc, "silver_enderecos": vinc,
            "silver_correlacoes_pendentes": pend, "silver_inconsistencias": inc,
            "gold_cidadao_sistemas": len(criados) + sem_cpf,
            "lidos": sum(len(v) for v in lidos.values())}
