"""Lotação → secretaria/órgão por SEGMENTOS do codloc (FL5, FL8, FL9). Ponto único da regra para o drill da HE (onda 1).

Regra (aprovada no canal Dados após a C4c):
- secretaria = 2 primeiros segmentos separados por '.' (`1.x`, qualquer número de dígitos: `1.10` é Saúde);
- órgão = 3 primeiros segmentos (`1.x.y`);
- descendente de `sec` = igual a `sec` ou começa com `sec + '.'`. NUNCA prefixo solto (`LIKE '1.1%'`) nem `LEFT(codloc, n)`:
  isso põe a Saúde (`1.10`) dentro do Gabinete (`1.1`).
- raiz `1`, vazio ou código fora do de-para → "Sem lotação" (entra no total, fora do ranking).
- 1.12–1.15 → "Outros vínculos": fora do ranking e da média da prefeitura; entram no total.

O de-para mora em `ref.lotacao_secretaria` (migração 0045); `DE_PARA_PADRAO` é a mesma estrutura, para uso sem banco.
"""
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.paineis.privacidade import suprime

SECRETARIA, OUTROS_VINCULOS, SEM_LOTACAO = "secretaria", "outros_vinculos", "sem_lotacao"
ROTULO_OUTROS_VINCULOS = "Outros vínculos"
ROTULO_SEM_LOTACAO = "Sem lotação"
RAIZ = "1"

# (codloc_secretaria, nome, grupo, ordem) — espelho da semente da 0045
DE_PARA_PADRAO: tuple[tuple[str, str, str, int], ...] = (
    ("1", ROTULO_SEM_LOTACAO, SEM_LOTACAO, 99),
    ("1.1", "Gabinete do Prefeito", SECRETARIA, 1), ("1.2", "Procuradoria Geral", SECRETARIA, 2),
    ("1.3", "Ação Social", SECRETARIA, 3), ("1.4", "Administração e RH", SECRETARIA, 4),
    ("1.5", "Inovação e Desenvolvimento", SECRETARIA, 5), ("1.6", "Educação", SECRETARIA, 6),
    ("1.7", "Finanças", SECRETARIA, 7), ("1.8", "Infraestrutura", SECRETARIA, 8),
    ("1.9", "Meio Ambiente", SECRETARIA, 9), ("1.10", "Saúde", SECRETARIA, 10),
    ("1.11", "Segurança", SECRETARIA, 11), ("1.12", "Cedidos", OUTROS_VINCULOS, 51),
    ("1.13", "Aposentados", OUTROS_VINCULOS, 52), ("1.14", "Pensionistas", OUTROS_VINCULOS, 53),
    ("1.15", "Indenizados", OUTROS_VINCULOS, 54), ("1.16", "Esporte e Cultura", SECRETARIA, 12),
)


@dataclass(frozen=True)
class Lotacao:
    codloc: str
    secretaria: str | None  # código `1.x` (None = sem lotação)
    orgao: str | None       # código `1.x.y` (None se o nó é a própria secretaria ou sem lotação)
    nome: str               # nome da secretaria ou rótulo do grupo
    grupo: str              # secretaria | outros_vinculos | sem_lotacao

    @property
    def no_ranking(self) -> bool:
        return self.grupo == SECRETARIA


def segmentos(codloc: str | None) -> list[str]:
    return [s for s in (codloc or "").strip().split(".")] if (codloc or "").strip() else []


def descendente(codloc: str | None, sec: str) -> bool:
    c = (codloc or "").strip()
    return c == sec or c.startswith(sec + ".")


def sql_descendente(coluna: str = "codloc", parametro: str = "sec") -> str:
    """Filtro SQL por segmento (bind `:sec`). Nunca `LIKE :sec || '%'`."""
    return f"({coluna} = :{parametro} OR {coluna} LIKE :{parametro} || '.%')"


def carrega_de_para(db: Session) -> tuple[tuple[str, str, str, int], ...]:
    return tuple(db.execute(text("SELECT codloc_secretaria, nome, grupo, ordem FROM ref.lotacao_secretaria ORDER BY ordem")))


class Resolvedor:
    def __init__(self, de_para: Iterable[tuple[str, str, str, int]] = DE_PARA_PADRAO):
        self.mapa = {c: (n, g, o) for c, n, g, o in de_para}

    def resolve(self, codloc: str | None) -> Lotacao:
        c = (codloc or "").strip()
        seg = segmentos(c)
        if len(seg) < 2 or seg[0] != RAIZ or not all(seg):
            return Lotacao(c, None, None, ROTULO_SEM_LOTACAO, SEM_LOTACAO)
        sec = ".".join(seg[:2])
        if sec not in self.mapa:
            return Lotacao(c, None, None, ROTULO_SEM_LOTACAO, SEM_LOTACAO)
        nome, grupo, _ = self.mapa[sec]
        if grupo == OUTROS_VINCULOS:
            nome = ROTULO_OUTROS_VINCULOS
        orgao = ".".join(seg[:3]) if len(seg) >= 3 else None
        return Lotacao(c, sec, orgao, nome, grupo)

    def ordem(self, sec: str) -> int:
        return self.mapa.get(sec, ("", "", 999))[2]


def agrupa_drill(linhas: Iterable[dict], k: int, resolvedor: Resolvedor | None = None,
                 medidas: tuple[str, ...] = ("m1", "m2")) -> dict:
    """`linhas`: dicts sintéticos/agregados com `codloc`, `n` e medidas aditivas (nunca pessoa).

    Devolve ranking (só secretarias, com k aplicado), blocos "Outros vínculos" e "Sem lotação" (k aplicado por grupo),
    total geral (soma de tudo) e média da prefeitura (só secretarias do ranking)."""
    r = resolvedor or Resolvedor()
    acc: dict[tuple[str, str], dict] = {}
    for ln in linhas:
        lot = r.resolve(ln.get("codloc"))
        chave = (lot.grupo, lot.secretaria if lot.grupo == SECRETARIA else lot.grupo)
        a = acc.setdefault(chave, {"rotulo": lot.nome, "secretaria": lot.secretaria if lot.grupo == SECRETARIA else None,
                                   "grupo": lot.grupo, "n": 0, **{m: 0.0 for m in medidas}})
        a["n"] += int(ln.get("n") or 0)
        for m in medidas:
            a[m] += float(ln.get(m) or 0)
    secs = sorted((v for (g, _), v in acc.items() if g == SECRETARIA), key=lambda v: r.ordem(v["secretaria"]))
    ranking, _ = suprime(secs, k, medidas)
    blocos = {}
    for g in (OUTROS_VINCULOS, SEM_LOTACAO):
        v = acc.get((g, g))
        blocos[g] = None if v is None else ({**v, "protegido": True, **{m: None for m in medidas}} if v["n"] < k else v)
    total = {"n": sum(v["n"] for v in acc.values()), **{m: sum(v[m] for v in acc.values()) for m in medidas}}
    pref = {"n": sum(v["n"] for v in secs), **{m: sum(v[m] for v in secs) for m in medidas}}
    pref["media_m2"] = (pref["m2"] / pref["n"]) if "m2" in medidas and pref["n"] else None
    return {"ranking": ranking, "outros_vinculos": blocos[OUTROS_VINCULOS], "sem_lotacao": blocos[SEM_LOTACAO],
            "total": total, "prefeitura": pref}
