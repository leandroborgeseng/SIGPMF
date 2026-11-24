"""Carga idempotente do CSV do cadastro físico de imóveis da SIL.

Não chama a API. Upsert em bronze.sil_imoveis_fisico por idfisico e registra a execução
em controle.execucoes (sistema SIL). Rodar de novo não duplica.

Dentro do container do backend (stdin não deixa o arquivo no disco do container):

    docker compose exec -T backend python -m scripts.carga_sil_imoveis --arquivo - --usuario "Nome do operador" < /caminho/no/host/sil.csv

Arquivo já visível no container:

    python -m scripts.carga_sil_imoveis --arquivo /caminho/sil.csv --usuario "Nome do operador"

O mesmo CSV pode ser enviado em Cargas > Importar imóveis SIL (só administrador).
"""
from __future__ import annotations

import argparse
import io
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Carga do CSV do cadastro físico de imóveis da SIL.")
    p.add_argument("--arquivo", required=True, help="caminho do CSV latin1, ou - para ler o stdin")
    p.add_argument("--usuario", default=None, help="operador gravado em controle.execucoes")
    a = p.parse_args(argv)
    from app.core.db import SessionLocal
    from app.services.carga_sil_imoveis import ArquivoInvalido, carregar

    stream = None
    try:
        if a.arquivo == "-":
            stream = io.TextIOWrapper(sys.stdin.buffer, encoding="latin-1", newline="")
            origem = stream
        else:
            origem = a.arquivo
        with SessionLocal() as db:
            ex = carregar(db, origem, usuario=a.usuario)
            resumo = (f"OK: execucao={ex.id} status={ex.status} lidos={ex.lidos} novos={ex.novos} "
                      f"atualizados={ex.atualizados} ignorados={ex.ignorados} "
                      f"duplicados={ex.duplicados} lotes={ex.lotes}")
    except FileNotFoundError:
        print("Não foi possível abrir o arquivo.", file=sys.stderr)
        return 2
    except ArquivoInvalido as exc:
        print(f"RECUSADO: {exc}", file=sys.stderr)
        return 2
    except OSError:
        print("Não foi possível abrir o arquivo.", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 — o detalhe fica na execução; o stderr não leva dado de linha
        print("Falha na carga do cadastro físico da SIL.", file=sys.stderr)
        return 1
    finally:
        if stream is not None:
            stream.detach()
    print(resumo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
