"""Leitura de recursos do servidor (disco onde ficam os volumes do Postgres, memória do container, tamanho do banco).

Módulo compartilhado: usado pela trava da carga completa (recusa/pausa com disco >= limite) e pelo card
"Recursos do servidor" (GET /api/sistema/recursos). Nunca devolve caminhos nem hostnames, só números.
Falha de leitura vira None (a UI mostra "Sem leitura do servidor", nunca 0%).

Variáveis de ambiente (todas opcionais):
- RECURSOS_DISCO_CAMINHO: volume vazio `srvdisk`, montado só leitura no mesmo disco dos volumes do Docker
  (onde fica o pgdata), padrão /srvdisk. O backend nunca monta o pgdata. Caminho inexistente = sem leitura.
- RECURSOS_LIMITE_DISCO: % de disco usado acima do qual a carga completa não inicia (RS3, padrão 85).
- RECURSOS_LIMITE_PAUSA: % de disco usado acima do qual a carga em andamento pausa sozinha (RS4, padrão 90).
- RECURSOS_AMARELO / RECURSOS_VERMELHO: faixas de cor do card (padrão 70 / 85).
"""
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

GB = 1024 ** 3
_SEM_LIMITE = 1 << 60  # cgroup "max" ou valores absurdos = sem limite próprio


def _int_env(nome: str, padrao: int) -> int:
    try:
        v = int(os.environ.get(nome, padrao))
    except ValueError:
        return padrao
    return v if 1 <= v <= 100 else padrao


def limite_disco() -> int:
    return _int_env("RECURSOS_LIMITE_DISCO", 85)


def limite_pausa() -> int:
    return _int_env("RECURSOS_LIMITE_PAUSA", 90)


def faixas() -> tuple[int, int]:
    amarelo, vermelho = _int_env("RECURSOS_AMARELO", 70), _int_env("RECURSOS_VERMELHO", 85)
    return (amarelo, vermelho) if amarelo < vermelho else (70, 85)


@dataclass(frozen=True)
class Uso:
    total: int  # bytes
    livre: int  # bytes

    @property
    def usado_pct(self) -> float:
        return round(100 * (self.total - self.livre) / self.total, 1) if self.total else 0.0

    def publico(self) -> dict:
        return {"total_gb": round(self.total / GB, 2), "livre_gb": round(self.livre / GB, 2),
                "usado_pct": self.usado_pct, "status": status_pct(self.usado_pct)}


def status_pct(pct: float | None) -> str:
    if pct is None:
        return "sem_leitura"
    amarelo, vermelho = faixas()
    return "ok" if pct < amarelo else "atencao" if pct <= vermelho else "critico"  # RS2: verde/âmbar/vermelho


def disco() -> Uso | None:
    caminho = os.environ.get("RECURSOS_DISCO_CAMINHO", "/srvdisk")
    try:
        d = shutil.disk_usage(caminho)
    except OSError:
        return None
    return Uso(total=d.total, livre=d.free) if d.total > 0 else None


def _ler_int(p: Path) -> int | None:
    try:
        t = p.read_text().strip()
    except OSError:
        return None
    if t == "max":
        return None
    try:
        return int(t)
    except ValueError:
        return None


def _meminfo(raiz: Path) -> tuple[int, int] | None:
    try:
        linhas = (raiz / "proc/meminfo").read_text().splitlines()
    except OSError:
        return None
    vals = {}
    for linha in linhas:
        k, _, v = linha.partition(":")
        partes = v.split()
        if partes and partes[0].isdigit():
            vals[k.strip()] = int(partes[0]) * 1024
    if "MemTotal" in vals and "MemAvailable" in vals:
        return vals["MemTotal"], vals["MemAvailable"]
    return None


def memoria(raiz: str | Path = "/") -> Uso | None:
    """Memória que o container enxerga: limite do cgroup v2 (memory.max/current); sem limite, MemTotal/MemAvailable."""
    r = Path(raiz)
    info = _meminfo(r)
    maximo = _ler_int(r / "sys/fs/cgroup/memory.max")
    atual = _ler_int(r / "sys/fs/cgroup/memory.current")
    if maximo and atual is not None and 0 < maximo < _SEM_LIMITE:
        livre = max(maximo - atual, 0)
        if info:  # nunca mais livre do que o host realmente tem
            livre = min(livre, info[1])
        return Uso(total=maximo, livre=livre)
    if info:
        return Uso(total=info[0], livre=info[1])
    return None


SCHEMAS = {"bronze": "bronze", "silver": "silver", "gold": "gold", "controle": "controle"}


def tamanho_banco(db) -> dict[str, float] | None:
    """MB por camada: soma de pg_total_relation_size das tabelas de cada schema."""
    from sqlalchemy import text
    try:
        linhas = db.execute(text(
            "SELECT n.nspname, COALESCE(SUM(pg_total_relation_size(c.oid)), 0) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r','m','p') "
            "AND n.nspname = ANY(:s) GROUP BY n.nspname"), {"s": list(SCHEMAS.values())}).all()
    except Exception:  # noqa: BLE001
        db.rollback()
        return None
    por = {k: int(v) for k, v in linhas}
    return {k: round(por.get(s, 0) / 1024 ** 2, 1) for k, s in SCHEMAS.items()}


SEM_LEITURA_DISCO = "Sem leitura do disco do servidor. A carga completa fica bloqueada até o disco voltar a ser lido."


def motivo_disco_cheio(limite: int | None = None, exige_leitura: bool = False) -> str | None:
    """Motivo para não iniciar (limite padrão RS3) ou pausar (limite_pausa(), RS4). None = pode seguir.
    Com exige_leitura (carga completa), disco sem leitura também bloqueia (fail-closed)."""
    d = disco()
    lim = limite_disco() if limite is None else limite
    if d is None:
        return SEM_LEITURA_DISCO if exige_leitura else None
    if d.usado_pct > lim:
        return f"Disco do servidor com {d.usado_pct:.0f}% usado (limite {lim}%). Libere espaço e retome a carga."
    return None


def publico(db=None) -> dict:
    """Resposta de GET /api/sistema/recursos: só números e status, sem caminhos nem hostnames."""
    from datetime import UTC, datetime
    d, m = disco(), memoria()
    amarelo, vermelho = faixas()
    return {"lido_em": datetime.now(UTC).isoformat(), "faixas": {"amarelo": amarelo, "vermelho": vermelho},
            "limites": {"inicio_carga": limite_disco(), "pausa_carga": limite_pausa()},
            "disco": d.publico() if d else None, "memoria": m.publico() if m else None,
            "banco_mb": tamanho_banco(db) if db is not None else None}
