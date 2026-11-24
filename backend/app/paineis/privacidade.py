"""K-anonimato dos painéis (K01–K04): PONTO ÚNICO da regra para todos os cubos e painéis.

- `k_do_cubo(cubo)`: corte de cada cubo (padrão 5; saúde rara 10). Os cubos do ouro (app.gold.cubos_consulta) e o shell
  de painéis leem o corte daqui; não há outro número mágico de k no backend dos painéis.
- `suprime(itens, k)`: grupos com n < k não saem com valor; vão para "Outros (grupos pequenos)", que ENTRA na soma, então
  a soma dos filhos continua igual ao total do pai (K04/P05). Se o próprio "Outros" ficar com n < k, o menor grupo visível
  também entra nele (supressão complementar), para não dar para descobrir o grupo oculto pela diferença.
- `valor_protegido(n, k)`: o total de um recorte com n < k não é devolvido (texto de privacidade na tela).
"""
from collections.abc import Iterable

K_PADRAO = 5
K_SAUDE_RARA = 10
OUTROS = "Outros (grupos pequenos)"

# Cubos do ouro (CB2) + cubos do shell. Saúde rara (agravos raros) usa k >= 10.
K_POR_CUBO: dict[str, int] = {
    "pessoas": K_PADRAO, "saude": K_PADRAO, "imoveis": K_PADRAO, "tributario": K_PADRAO, "divida": K_PADRAO,
    "empresas": K_PADRAO, "educacao": K_PADRAO, "rh": K_PADRAO, "despesa": K_PADRAO, "receita": K_PADRAO,
    "saude_rara": K_SAUDE_RARA, "folha_modelo": K_PADRAO,
}


def k_do_cubo(cubo: str) -> int:
    """Cubo desconhecido cai no padrão (nunca abaixo de 5)."""
    return max(K_PADRAO, K_POR_CUBO.get(cubo, K_PADRAO))


def suprime(itens: Iterable[dict], k: int, medidas: tuple[str, ...] = ("m1", "m2")) -> tuple[list[dict], int]:
    """`itens`: dicts com `rotulo`, `n` e as medidas aditivas. Devolve (itens visíveis + "Outros" no fim, quantos grupos
    foram para "Outros"). Ordem dos visíveis: a de entrada."""
    lista = [dict(i) for i in itens]
    pequenos = [i for i in lista if (i.get("n") or 0) < k]
    visiveis = [i for i in lista if (i.get("n") or 0) >= k]
    if not pequenos:
        return visiveis, 0
    # complementar: "Outros" não pode ficar com n < k enquanto houver grupo visível para juntar
    while sum(i["n"] or 0 for i in pequenos) < k and visiveis:
        menor = min(visiveis, key=lambda i: (i["n"], str(i.get("rotulo"))))
        visiveis.remove(menor)
        pequenos.append(menor)
    outros = {"rotulo": OUTROS, "n": sum(i.get("n") or 0 for i in pequenos), "outros": True, "grupos": len(pequenos)}
    for m in medidas:
        outros[m] = sum(float(i.get(m) or 0) for i in pequenos)
    return [*visiveis, outros], len(pequenos)


def valor_protegido(n: int | None, k: int) -> bool:
    """Verdadeiro quando o recorte tem menos de k pessoas: o valor não sai na resposta."""
    return (n or 0) < k
