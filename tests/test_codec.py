import pytest

from mini_smartex import codec


def test_float32_abcd_exemplo_conhecido():
    # 25.5 em IEEE 754 = 0x41CC0000 -> registradores 0x41CC e 0x0000
    assert codec.float32_para_registradores(25.5, "ABCD") == [0x41CC, 0x0000]


def test_float32_cdab_troca_as_palavras():
    assert codec.float32_para_registradores(25.5, "CDAB") == [0x0000, 0x41CC]


@pytest.mark.parametrize("ordem", ["ABCD", "CDAB"])
@pytest.mark.parametrize("valor", [0.0, 25.5, 195.3, -12.75, 8.012])
def test_float32_ida_e_volta(valor, ordem):
    regs = codec.float32_para_registradores(valor, ordem)
    assert codec.registradores_para_float32(regs, ordem) == pytest.approx(valor, rel=1e-6)


def test_float32_ordem_errada_da_valor_absurdo():
    regs = codec.float32_para_registradores(185.0, "ABCD")
    lido_errado = codec.registradores_para_float32(regs, "CDAB")
    assert lido_errado != pytest.approx(185.0)


def test_uint16_escalado():
    assert codec.valor_para_uint16(123.4, escala=0.1) == 1234
    assert codec.uint16_para_valor(1234, escala=0.1) == pytest.approx(123.4)


def test_uint16_satura_como_um_clp():
    assert codec.valor_para_uint16(-5) == 0
    assert codec.valor_para_uint16(70000) == 65535


def test_bits():
    reg = 0
    reg = codec.ligar_bit(reg, 0, True)
    reg = codec.ligar_bit(reg, 2, True)
    assert reg == 0b101
    assert codec.ler_bit(reg, 0) and not codec.ler_bit(reg, 1) and codec.ler_bit(reg, 2)
    assert codec.ligar_bit(reg, 0, False) == 0b100
