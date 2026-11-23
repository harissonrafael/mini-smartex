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
- Sinal analógico 4–20 mA (atividade de 20/11): o transmissor converte a
  grandeza física numa corrente; o cartão analógico do CLP mede a corrente
  e a entrega num registrador. Aqui o cartão entrega em µA (4000 a 20000).

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


# --------------------------------------------------------------------------
# Sinais analógicos 4–20 mA
# --------------------------------------------------------------------------
# Por que 4 mA e não 0 mA? Porque 0 mA passa a significar "sem sinal":
# fio rompido, transmissor sem alimentação. Esse "zero vivo" permite
# diagnosticar o instrumento, coisa que um sinal 0–10 V não permite.
#
# Faixas da recomendação NAMUR NE43 (padrão de fato na indústria):
#   3,8 a 20,5 mA  -> faixa de medição (abaixo de 4 / acima de 20 = saturado)
#   < 3,6 mA       -> falha (ex.: fio rompido)
#   > 21,0 mA      -> falha (ex.: curto-circuito)
#   entre esses    -> zona indefinida: não confie no valor

CORRENTE_MIN_MA = 4.0
CORRENTE_MAX_MA = 20.0

DIAG_OK = "ok"
DIAG_SATURADO = "saturado"
DIAG_INDEFINIDO = "indefinido"
DIAG_FALHA_BAIXA = "falha: corrente baixa (fio rompido?)"
DIAG_FALHA_ALTA = "falha: corrente alta (curto?)"
DIAGNOSTICOS_DE_FALHA = (DIAG_FALHA_BAIXA, DIAG_FALHA_ALTA, DIAG_INDEFINIDO)


def valor_para_corrente(valor: float, minimo: float, maximo: float) -> float:
    """Faz o papel do transmissor: 250 bar numa faixa 0–500 -> 12 mA.

    Fora da faixa calibrada, o transmissor satura em 3,8 / 20,5 mA (NE43).
    """
    ma = CORRENTE_MIN_MA + (valor - minimo) / (maximo - minimo) * (CORRENTE_MAX_MA - CORRENTE_MIN_MA)
    return max(3.8, min(20.5, ma))


def corrente_para_valor(ma: float, minimo: float, maximo: float) -> float:
    """Inverso: 12 mA numa faixa 0–500 bar -> 250 bar. Interpolação linear."""
    return minimo + (ma - CORRENTE_MIN_MA) / (CORRENTE_MAX_MA - CORRENTE_MIN_MA) * (maximo - minimo)


def diagnostico_ne43(ma: float) -> str:
    """Classifica a corrente segundo a NAMUR NE43."""
    if ma < 3.6:
        return DIAG_FALHA_BAIXA
    if ma > 21.0:
        return DIAG_FALHA_ALTA
    if ma < 3.8 or ma > 20.5:
        return DIAG_INDEFINIDO
    if ma < CORRENTE_MIN_MA or ma > CORRENTE_MAX_MA:
        return DIAG_SATURADO
    return DIAG_OK


# Convenção Siemens (cartões analógicos S7): a entrada vem como inteiro com
# sinal de 16 bits. Em 4–20 mA, 4 mA = 0 e 20 mA = 27648 (valor nominal).
# Acima disso há margem de overrange até 32511 (~22,81 mA) e abaixo, de
# underrange até -4864 (~1,19 mA). Fora desses limites o cartão sinaliza
# erro (32767 = overflow/fio rompido, conforme a configuração).
SIEMENS_FUNDO_ESCALA = 27648
SIEMENS_OVERRANGE_MAX = 32511
SIEMENS_UNDERRANGE_MIN = -4864


def contagem_siemens_para_ma(contagem: int) -> float:
    """0 -> 4 mA; 27648 -> 20 mA; 13824 -> 12 mA.

    Aceita a contagem como o CLP a entrega (inteiro com sinal). Um registrador
    Modbus lido como uint16 chega como 65535 para -1: converta antes com
    `registrador_para_int16`. Valores além do overrange/underrange não são
    medição válida e levantam ValueError.
    """
    if not SIEMENS_UNDERRANGE_MIN <= contagem <= SIEMENS_OVERRANGE_MAX:
        raise ValueError(
            f"contagem {contagem} fora de {SIEMENS_UNDERRANGE_MIN}..{SIEMENS_OVERRANGE_MAX} "
            "(overflow, underflow ou fio rompido no cartão)"
        )
    return CORRENTE_MIN_MA + contagem / SIEMENS_FUNDO_ESCALA * (CORRENTE_MAX_MA - CORRENTE_MIN_MA)


def ma_para_contagem_siemens(ma: float) -> int:
    """Inverso: 12 mA -> 13824. Satura nos limites de under/overrange."""
    contagem = round((ma - CORRENTE_MIN_MA) / (CORRENTE_MAX_MA - CORRENTE_MIN_MA) * SIEMENS_FUNDO_ESCALA)
    return max(SIEMENS_UNDERRANGE_MIN, min(SIEMENS_OVERRANGE_MAX, contagem))


def registrador_para_int16(registrador: int) -> int:
    """Complemento de dois: 65535 -> -1; 0x8000 -> -32768."""
    return registrador - 0x10000 if registrador >= 0x8000 else registrador


def ma_para_microampere(ma: float) -> int:
    """12.0 mA -> 12000 µA (cabe num registrador de 16 bits)."""
    return max(0, min(65535, round(ma * 1000)))


def microampere_para_ma(ua: int) -> float:
    return ua / 1000
