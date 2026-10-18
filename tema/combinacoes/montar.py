"""Combinações de peças, 18/10/2026: a lista do Pedro, nos dois sentidos.

Fonte única para a página de Combinações e o bloco "Combina com" do pé da
página de produto. A ordem da lista dele vale como prioridade; o espelho
(combinação que só aparecia num sentido) entra depois, no fim da lista.

    python tema/combinacoes/montar.py      gera combinacoes.json ao lado
"""
import json
import os

# Nome curto da lista do Pedro -> título do produto na Shopify.
P = {
    "colete trama marrom": "Colete de Jacquard Trama - Marrom",
    "colete trama vinho": "Colete de Jacquard Trama - Vinho",
    "calça trama marrom": "Calça de Jacquard Trama - Marrom",
    "calça trama vinho": "Calça de Jacquard Trama - Vinho",
    "calça joan bege": "Calça de Linho Bege - Joan",
    "calça joan marrom": "Calça de Linho Marrom - Joan",
    "calça georgia cru": "Calça de Linho Cru - Georgia",      # "Geórgia bege" (Pedro, 18/10)
    "calça georgia oliva": "Calça de Linho Oliva - Georgia",  # "Geórgia verde"
    "camisa herança branca": "Camisa de Algodão Herança — Branca",
    "camisa herança rosa": "Camisa de Algodão Herança — Rosa",
    "top bloom bege": "Top de Linho Bege - Bloom",
    "top bloom marrom": "Top de Linho Marrom - Bloom",
    "corset bege": "Corset de Linho Bege - Joan",
    "corset marrom": "Corset de Linho Marrom - Joan",
    "colete klint": "Colete de Jacquard Estampado - Klint",
    "blazer bege": "Blazer Feminino de Linho Bege - Delaunay",
    "blazer marrom": "Blazer Feminino de Linho Marrom - Delaunay",
    "vestido relicário vinho": "Vestido de Jacquard Relicário — Vinho",
    "vestido relicário cinza": "Vestido de Jacquard Relicário — Cinza",
    "vestido monet dia": "Vestido de Jacquard Monet - Dia",
    "vestido monet aurora": "Vestido de Jacquard Monet - Aurora",
    "vestido monet névoa": "Vestido de Jacquard Monet - Névoa",
    "saia giverny rosé": "Saia de Jacquard Rosé - Giverny",
    "saia giverny grafite": "Saia de Jacquard Grafite - Giverny",
    "top giverny grafite": "Top de Jacquard Grafite - Giverny",
    "top giverny rosé": "Top de Jacquard Rosé - Giverny",
}

# Lista do Pedro, 18/10/2026, na ordem dele.
LISTA = {
    "colete trama marrom": ["calça trama marrom", "calça joan bege", "camisa herança branca", "camisa herança rosa"],
    "calça trama marrom": ["colete trama marrom", "camisa herança branca", "camisa herança rosa", "top bloom bege",
                           "corset bege", "colete klint"],
    "colete trama vinho": ["calça trama vinho", "camisa herança rosa", "camisa herança branca"],
    "calça trama vinho": ["colete trama vinho", "camisa herança branca", "camisa herança rosa", "top bloom bege",
                          "colete klint"],
    "vestido relicário vinho": ["camisa herança rosa", "camisa herança branca"],
    "vestido relicário cinza": ["camisa herança branca"],
    "camisa herança rosa": ["calça joan bege", "calça georgia cru", "saia giverny rosé", "calça trama vinho",
                            "calça trama marrom", "vestido relicário vinho", "vestido monet dia"],
    "camisa herança branca": ["calça joan bege", "calça joan marrom", "calça georgia cru", "calça georgia oliva",
                              "colete klint", "saia giverny grafite", "saia giverny rosé", "calça trama vinho",
                              "calça trama marrom", "vestido relicário vinho", "vestido monet dia"],
    "saia giverny rosé": ["top bloom bege", "top bloom marrom", "camisa herança rosa", "camisa herança branca",
                          "corset marrom"],
    "saia giverny grafite": ["top bloom bege", "top bloom marrom", "top giverny grafite", "camisa herança branca",
                             "corset bege"],
    "top giverny grafite": ["calça joan bege", "calça joan marrom", "saia giverny grafite", "camisa herança branca",
                            "calça trama marrom"],
    "calça joan marrom": ["top bloom marrom", "blazer marrom", "colete klint", "top giverny rosé",
                          "top giverny grafite", "camisa herança branca", "corset marrom"],
    "calça joan bege": ["top bloom bege", "blazer bege", "colete klint", "corset bege", "camisa herança rosa",
                        "camisa herança branca"],
    # Lançamento do Monet (Pedro, 18/10): as duas cores novas com as camisas Herança.
    "vestido monet aurora": ["camisa herança branca", "camisa herança rosa"],
    "vestido monet névoa": ["camisa herança branca", "camisa herança rosa"],
}


def montar() -> dict:
    combina = {k: list(v) for k, v in LISTA.items()}
    for a, lista in LISTA.items():          # espelho: se A combina com B, B combina com A
        for b in lista:
            combina.setdefault(b, [])
            if a not in combina[b]:
                combina[b].append(a)
    return {P[a]: [P[b] for b in bs] for a, bs in sorted(combina.items())}


if __name__ == "__main__":
    saida = os.path.join(os.path.dirname(os.path.abspath(__file__)), "combinacoes.json")
    with open(saida, "w", encoding="utf-8") as f:
        json.dump(montar(), f, ensure_ascii=False, indent=2)
    print(saida)
