"""Releitura de UMA fonte da educação (ex.: PES_PessoaDocumento), sem carga completa das outras.

Só mexe em controle.checkpoints_carga (via carga_gestaoescolar.prepara_releitura): a fonte pedida volta ao início e as
outras 12 ficam concluídas. Nada é apagado da bronze. Não lê a origem; quem lê é a próxima carga completa da educação
(manual ou agenda), que, ao fechar o ciclo, reconstrói a prata da educação a partir da bronze.

Uso (dentro do container do backend, com o DATABASE_URL do app):
    python -m scripts.releitura_fonte_educacao --fonte PES_PessoaDocumento --usuario "Nome do operador"
Recusa (saída 2, nada alterado) se houver carga completa da educação em andamento ou a fonte não existir.
Só imprime nomes de tabela e contagens.
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Prepara a releitura de uma fonte da educação (só checkpoints).")
    p.add_argument("--fonte", required=True, help="nome da fonte, ex.: PES_PessoaDocumento")
    p.add_argument("--usuario", required=True, help="nome do operador (vai para a auditoria)")
    a = p.parse_args(argv)
    from app.core.db import SessionLocal
    from app.services import carga_gestaoescolar as cge
    with SessionLocal() as db:
        try:
            r = cge.prepara_releitura(db, a.fonte, a.usuario)
        except cge.CargaRecusada as e:
            print(f"RECUSADO (nada alterado): {e}", file=sys.stderr)
            return 2
    print(f"OK: {r['fonte']} volta ao início; {r['outras_concluidas']} outras fontes ficam concluídas. "
          "Rode a carga completa da educação (tela ou agenda) para ler só essa fonte e reconstruir a prata.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
