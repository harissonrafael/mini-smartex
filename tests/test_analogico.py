"""Testes do sinal analógico 4–20 mA (atividade de 20/11)."""

import pytest

from mini_smartex import codec, mapa
from mini_smartex.dicionario import Tag
from mini_smartex.processo import (
    CORRENTE_CURTO_MA,
    CORRENTE_FIO_ROMPIDO_MA,
    PRESSAO_MASSA,
    Extrusora,
)

PRESSAO = Tag(
    tag=PRESSAO_MASSA, nome="Pressão", unidade="bar", tipo_bruto="AI_4_20MA", registrador=14,
    bit=None, ordem_palavras="ABCD", escala=1.0, offset=0.0, frequencia_s=1.0,
    faixa_min=0.0, faixa_max=500.0, faixa_instrumento=(0.0, 500.0),
)


@pytest.mark.parametrize(
    "bar, ma",
    [(0, 4.0), (125, 8.0), (250, 12.0), (500, 20.0)],
)
def test_escala_linear(bar, ma):
    assert codec.valor_para_corrente(bar, 0, 500) == pytest.approx(ma)
    assert codec.corrente_para_valor(ma, 0, 500) == pytest.approx(bar)


def test_faixa_que_nao_comeca_em_zero():
    # Termopar com transmissor calibrado de 50 a 350 °C: 4 mA = 50 °C
    assert codec.corrente_para_valor(4.0, 50, 350) == pytest.approx(50)
    assert codec.corrente_para_valor(12.0, 50, 350) == pytest.approx(200)


def test_transmissor_satura_fora_da_faixa():
    assert codec.valor_para_corrente(-100, 0, 500) == 3.8
    assert codec.valor_para_corrente(900, 0, 500) == 20.5


@pytest.mark.parametrize(
    "ma, esperado",
    [
        (12.0, codec.DIAG_OK),
        (3.9, codec.DIAG_SATURADO),
        (20.3, codec.DIAG_SATURADO),
        (3.7, codec.DIAG_INDEFINIDO),
        (0.0, codec.DIAG_FALHA_BAIXA),
        (22.0, codec.DIAG_FALHA_ALTA),
    ],
)
def test_diagnostico_ne43(ma, esperado):
    assert codec.diagnostico_ne43(ma) == esperado


def test_registrador_em_microampere():
    regs = mapa.montar_registradores([PRESSAO], {PRESSAO_MASSA: 250.0})
    assert regs == {14: 12000}  # 12 mA


def test_fio_rompido_vira_none_e_nao_lixo():
    regs = mapa.montar_registradores([PRESSAO], {PRESSAO_MASSA: 250.0}, {PRESSAO_MASSA: 0.0})
    assert regs == {14: 0}
    assert mapa.decodificar([PRESSAO], regs)[PRESSAO_MASSA] is None
    ma, diag = mapa.diagnosticar([PRESSAO], regs)[PRESSAO_MASSA]
    assert ma == 0 and diag == codec.DIAG_FALHA_BAIXA


def test_curto_vira_none_com_diagnostico_de_corrente_alta():
    regs = mapa.montar_registradores(
        [PRESSAO], {PRESSAO_MASSA: 250.0}, {PRESSAO_MASSA: CORRENTE_CURTO_MA}
    )
    assert regs == {14: 22000}
    assert mapa.decodificar([PRESSAO], regs)[PRESSAO_MASSA] is None
    ma, diag = mapa.diagnosticar([PRESSAO], regs)[PRESSAO_MASSA]
    assert ma == 22 and diag == codec.DIAG_FALHA_ALTA


def test_extrusora_informa_a_corrente_do_defeito_e_volta_ao_normal():
    extrusora = Extrusora(semente=3, falhas_aleatorias=False)
    extrusora.forcar_falha_sensor(PRESSAO_MASSA, 2, CORRENTE_CURTO_MA)
    extrusora.passo()
    assert extrusora.sensores_com_falha == {PRESSAO_MASSA: CORRENTE_CURTO_MA}
    extrusora.passo(2)  # defeito acabou
    assert extrusora.sensores_com_falha == {}
    extrusora.forcar_falha_sensor(PRESSAO_MASSA, 5)  # padrão: fio rompido
    assert extrusora.sensores_com_falha == {PRESSAO_MASSA: CORRENTE_FIO_ROMPIDO_MA}


def test_pressao_acompanha_a_rotacao_e_falha_de_sensor_nao_para_a_linha():
    extrusora = Extrusora(semente=3, falhas_aleatorias=False)
    normal = extrusora.passo()
    assert normal[PRESSAO_MASSA] == pytest.approx(250, abs=15)

    extrusora.forcar_falha_sensor(PRESSAO_MASSA, 10)
    valores = extrusora.passo()
    assert PRESSAO_MASSA in extrusora.sensores_com_falha
    assert valores["LIN01_VELOCIDADE"] == pytest.approx(300, abs=5)  # linha segue andando
