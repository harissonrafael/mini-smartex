import pytest

from mini_smartex import processo
from mini_smartex.processo import Extrusora


def test_operacao_normal_fica_perto_do_nominal():
    extrusora = Extrusora(semente=7, falhas_aleatorias=False)
    for _ in range(120):
        valores = extrusora.passo()
    assert valores[processo.TEMP_ZONA1] == pytest.approx(170, abs=2)
    assert valores[processo.ROSCA_RPM] == pytest.approx(60, abs=1)
    assert valores[processo.VELOCIDADE] == pytest.approx(300, abs=5)
    assert valores[processo.DIAMETRO] == pytest.approx(8.0, abs=0.2)
    assert not any(valores[t] for t in processo.FALHAS)


def test_falha_da_extrusora_para_a_linha_e_volta():
    extrusora = Extrusora(semente=7, falhas_aleatorias=False)
    extrusora.forcar_falha(processo.FALHA_EXTRUSORA, 30)
    for _ in range(15):
        valores = extrusora.passo()
    assert valores[processo.FALHA_EXTRUSORA] is True
    assert valores[processo.ROSCA_RPM] == 0
    assert valores[processo.VELOCIDADE] == 0
    assert valores[processo.TEMP_ZONA1] < 170  # esfriando devagar

    for _ in range(60):
        valores = extrusora.passo()
    assert valores[processo.FALHA_EXTRUSORA] is False
    assert valores[processo.ROSCA_RPM] == pytest.approx(60, abs=1)


def test_mesma_semente_mesmos_valores():
    a, b = Extrusora(semente=42), Extrusora(semente=42)
    assert [a.passo() for _ in range(50)] == [b.passo() for _ in range(50)]
