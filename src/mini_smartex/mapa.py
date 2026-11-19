"""Mapa de registradores: liga o dicionário (tags) aos registradores Modbus.

- `montar_registradores`: valores de engenharia -> registradores (usado pelo simulador).
- `decodificar`: registradores -> valores de engenharia (usado pelo leitor).

As duas funções não dependem do pymodbus, então dá para testar sem rede.
"""

from __future__ import annotations

from mini_smartex import codec
from mini_smartex.dicionario import Tag


def montar_registradores(tags: list[Tag], valores: dict[str, float | bool]) -> dict[int, int]:
    """Devolve {endereço: valor de 16 bits} para todas as tags.

    Tags sem valor em `valores` ficam em 0 (o simulador avisa no log).
    """
    registradores: dict[int, int] = {}
    for t in tags:
        valor = valores.get(t.tag, 0)
        if t.tipo_bruto == "FLOAT32":
            alta, baixa = codec.float32_para_registradores(float(valor), t.ordem_palavras)
            registradores[t.registrador] = alta
            registradores[t.registrador + 1] = baixa
        elif t.tipo_bruto == "UINT16":
            registradores[t.registrador] = codec.valor_para_uint16(float(valor), t.escala, t.offset)
        elif t.tipo_bruto == "BOOL":
            atual = registradores.get(t.registrador, 0)
            registradores[t.registrador] = codec.ligar_bit(atual, t.bit, bool(valor))
    return registradores


def decodificar(
    tags: list[Tag], registradores: dict[int, int], ordem_forcada: str | None = None
) -> dict[str, float | bool]:
    """Devolve {tag: valor de engenharia}.

    `ordem_forcada` ignora a ordem do dicionário. Serve para o exercício de
    ver o que acontece quando se lê um FLOAT32 com a ordem errada.
    """
    valores: dict[str, float | bool] = {}
    for t in tags:
        if t.tipo_bruto == "FLOAT32":
            par = [registradores[t.registrador], registradores[t.registrador + 1]]
            valores[t.tag] = codec.registradores_para_float32(par, ordem_forcada or t.ordem_palavras)
        elif t.tipo_bruto == "UINT16":
            valores[t.tag] = codec.uint16_para_valor(registradores[t.registrador], t.escala, t.offset)
        elif t.tipo_bruto == "BOOL":
            valores[t.tag] = codec.ler_bit(registradores[t.registrador], t.bit)
    return valores


def faixa_de_leitura(tags: list[Tag]) -> tuple[int, int]:
    """(primeiro endereço, quantidade) para ler tudo numa requisição só.

    Ler um bloco contíguo é bem mais eficiente que uma requisição por tag:
    cada requisição Modbus tem custo fixo de rede.
    """
    inicio = min(t.registrador for t in tags)
    fim = max(t.registrador + t.quantidade_registradores for t in tags)
    return inicio, fim - inicio


def agrupar_blocos(registradores: dict[int, int]) -> list[tuple[int, list[int]]]:
    """Agrupa endereços contíguos: {0:a, 1:b, 5:c} -> [(0, [a, b]), (5, [c])]."""
    blocos: list[tuple[int, list[int]]] = []
    for endereco in sorted(registradores):
        if blocos and blocos[-1][0] + len(blocos[-1][1]) == endereco:
            blocos[-1][1].append(registradores[endereco])
        else:
            blocos.append((endereco, [registradores[endereco]]))
    return blocos
