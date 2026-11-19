"""Conversão entre valores de engenharia e registradores Modbus de 16 bits.

Este é o coração da atividade de 19/11. Um registrador Modbus guarda só
16 bits (0 a 65535), então:

- FLOAT32 ocupa 2 registradores. A ordem em que o CLP coloca as duas metades
  varia por fabricante: ABCD (big-endian, a mais comum) ou CDAB (palavras
  trocadas). Ler com a ordem errada dá números absurdos.
- Inteiros escalados: o CLP manda 1234 e o dicionário diz "escala 0,1",
  então o valor real é 123,4.
- Bits: vários sinais liga/desliga (falhas, emergência) dividem o mesmo
  registrador, um bit para cada.

O pymodbus tem conversores prontos (client.convert_from_registers). Aqui
fazemos à mão com `struct` para entender o que acontece por baixo.
"""

from __future__ import annotations

import struct

ORDENS = ("ABCD", "CDAB")


def float32_para_registradores(valor: float, ordem: str = "ABCD") -> list[int]:
    """25.5 -> [16844, 0] em ABCD; [0, 16844] em CDAB."""
    alta, baixa = struct.unpack(">HH", struct.pack(">f", valor))
    return [alta, baixa] if _ordem(ordem) == "ABCD" else [baixa, alta]


def registradores_para_float32(registradores: list[int], ordem: str = "ABCD") -> float:
    """Inverso de float32_para_registradores."""
    if len(registradores) != 2:
        raise ValueError("FLOAT32 precisa de exatamente 2 registradores")
    primeiro, segundo = registradores
    alta, baixa = (primeiro, segundo) if _ordem(ordem) == "ABCD" else (segundo, primeiro)
    return struct.unpack(">f", struct.pack(">HH", alta, baixa))[0]


def valor_para_uint16(valor: float, escala: float = 1.0, offset: float = 0.0) -> int:
    """123.4 rpm com escala 0.1 -> 1234. Satura em 0..65535, como um CLP faria."""
    bruto = round((valor - offset) / escala)
    return max(0, min(65535, bruto))


def uint16_para_valor(bruto: int, escala: float = 1.0, offset: float = 0.0) -> float:
    """1234 com escala 0.1 -> 123.4."""
    return bruto * escala + offset


def ligar_bit(registrador: int, bit: int, ligado: bool) -> int:
    """Liga ou desliga um bit sem mexer nos outros."""
    mascara = 1 << bit
    return registrador | mascara if ligado else registrador & ~mascara & 0xFFFF


def ler_bit(registrador: int, bit: int) -> bool:
    """Ex.: registrador 0b101 (5): bit 0 = True, bit 1 = False, bit 2 = True."""
    return bool((registrador >> bit) & 1)


def _ordem(ordem: str) -> str:
    ordem = ordem.upper()
    if ordem not in ORDENS:
        raise ValueError(f"ordem de palavras desconhecida: {ordem!r} (use {ORDENS})")
    return ordem
