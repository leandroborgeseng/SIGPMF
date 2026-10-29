"""Marca como 'sandbox' a bronze SIL gravada pelas amostras 25 e 31 (homologação), decisão TL 29/10.

Usado pela migration 0020 e pelos testes. Regras:
- só linhas sistema_origem='SIL' com execucao_id nas execuções informadas;
- conta antes: o total tem de ser igual à soma de (novos + atualizados) registrada nessas execuções (SIL) e
  no máximo TETO; senão levanta e não muda nada;
- idempotente: só atualiza o que ainda está 'producao'; nunca apaga;
- o hash da linha marcada ganha o prefixo 'sandbox:' para não colidir com a mesma linha vinda da produção
  (unique sistema/tabela/id_origem/hash), que assim entra como linha nova de produção."""
from sqlalchemy import text

EXECUCOES = (25, 31)
TETO = 98


class ContagemDivergente(RuntimeError):
    pass


def marcar(conn, execucoes: tuple[int, ...] = EXECUCOES, teto: int = TETO) -> dict:
    ids = list(execucoes)
    esperado = int(conn.execute(text(
        "SELECT COALESCE(SUM(COALESCE(novos,0) + COALESCE(atualizados,0)), 0) FROM controle.execucoes "
        "WHERE id = ANY(:ids) AND lower(sistema) = 'sil'"), {"ids": ids}).scalar() or 0)
    achado = int(conn.execute(text(
        "SELECT COUNT(*) FROM bronze.registros WHERE lower(sistema_origem) = 'sil' AND execucao_id = ANY(:ids)"),
        {"ids": ids}).scalar() or 0)
    if achado != esperado or achado > teto:
        raise ContagemDivergente(f"bronze SIL das execuções {ids}: {achado} linha(s), esperado {esperado} "
                                 f"(teto {teto}); nada foi alterado")
    marcadas = conn.execute(text(
        "UPDATE bronze.registros SET ambiente_origem = 'sandbox', "
        "hash = encode(sha256(convert_to('sandbox:' || hash, 'UTF8')), 'hex') "
        "WHERE lower(sistema_origem) = 'sil' AND execucao_id = ANY(:ids) AND ambiente_origem = 'producao'"),
        {"ids": ids}).rowcount
    return {"esperado": esperado, "encontradas": achado, "marcadas": int(marcadas or 0)}
