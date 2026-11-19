"""Modelo simplificado de uma linha de extrusão de cabos.

Não é física de verdade. É o suficiente para gerar dados com cara de
chão de fábrica:

- Temperaturas se aproximam do setpoint aos poucos (inércia térmica) e
  esfriam devagar quando a extrusora para.
- Rotação e velocidade sobem e descem em rampa, não de uma vez.
- O diâmetro depende da relação rotação/velocidade (mais material por metro
  = cabo mais grosso), tem ruído de medição e uma deriva lenta.
- Falhas e emergência acontecem ao acaso, duram um tempo e param a linha.

As chaves dos valores são as tags do dicionário de dados.
"""

from __future__ import annotations

import math
import random

# Tags do dicionário que este modelo sabe simular
TEMP_ZONA1 = "EXT01_TEMP_ZONA1"
TEMP_ZONA3 = "EXT01_TEMP_ZONA3"
TEMP_COLAR = "EXT01_TEMP_COLAR"
TEMP_CABECA = "EXT01_TEMP_CABECA"
ROSCA_RPM = "EXT01_ROSCA_RPM"
VELOCIDADE = "LIN01_VELOCIDADE"
DIAMETRO = "LIN01_DIAMETRO"
FALHA_EXTRUSORA = "EXT01_FALHA"
FALHA_PUXADOR = "LIN01_FALHA_PUXADOR"
EMERGENCIA = "LIN01_EMERGENCIA"
PRESSAO_MASSA = "EXT01_PRESSAO_MASSA"

TAGS_SIMULADAS = (
    TEMP_ZONA1, TEMP_ZONA3, TEMP_COLAR, TEMP_CABECA,
    ROSCA_RPM, VELOCIDADE, DIAMETRO,
    FALHA_EXTRUSORA, FALHA_PUXADOR, EMERGENCIA,
    PRESSAO_MASSA,
)

SETPOINTS_TEMPERATURA = {
    TEMP_ZONA1: 170.0,
    TEMP_ZONA3: 185.0,
    TEMP_COLAR: 190.0,
    TEMP_CABECA: 195.0,
}
TEMPERATURA_AMBIENTE = 25.0
RPM_NOMINAL = 60.0
VELOCIDADE_NOMINAL = 300.0  # m/min
DIAMETRO_NOMINAL = 8.0  # mm
PRESSAO_NOMINAL = 250.0  # bar, com rotação nominal e cabeçote no setpoint

# Corrente que o cartão enxerga com o transmissor defeituoso (ver NE43 no codec)
CORRENTE_FIO_ROMPIDO_MA = 0.0  # nenhuma corrente chega ao cartão
CORRENTE_CURTO_MA = 22.0  # acima de 21 mA: curto-circuito

# Falhas de instrumento (não param a linha, só estragam a medição)
FALHAS_SENSOR = {PRESSAO_MASSA: (0.0005, (20, 120))}

# Falhas: chance de começar a cada segundo e duração (min, max) em segundos
FALHAS = {
    FALHA_EXTRUSORA: (0.002, (10, 60)),
    FALHA_PUXADOR: (0.002, (10, 60)),
    EMERGENCIA: (0.0004, (30, 120)),
}


class Extrusora:
    """Estado da linha. Chame `passo()` a cada intervalo para avançar o tempo."""

    def __init__(self, semente: int | None = None, falhas_aleatorias: bool = True) -> None:
        self._rng = random.Random(semente)
        self._falhas_aleatorias = falhas_aleatorias
        self.temperaturas = dict(SETPOINTS_TEMPERATURA)  # começa já aquecida
        self.rpm = RPM_NOMINAL
        self.velocidade = VELOCIDADE_NOMINAL
        self.deriva_diametro = 0.0
        self.diametro = DIAMETRO_NOMINAL
        self.falhas: dict[str, float] = {}  # tag -> segundos restantes
        self.falhas_sensor: dict[str, float] = {}  # tag do sensor -> segundos restantes
        self._corrente_falha: dict[str, float] = {}  # tag do sensor -> mA durante o defeito

    @property
    def sensores_com_falha(self) -> dict[str, float]:
        """Sensores 4–20 mA com defeito neste instante: {tag: corrente em mA}."""
        return {
            tag: self._corrente_falha.get(tag, CORRENTE_FIO_ROMPIDO_MA) for tag in self.falhas_sensor
        }

    def forcar_falha_sensor(
        self, tag: str, segundos: float, corrente_ma: float = CORRENTE_FIO_ROMPIDO_MA
    ) -> None:
        """Simula um defeito no transmissor: fio rompido (0 mA, padrão) ou curto (22 mA)."""
        if tag not in FALHAS_SENSOR:
            raise ValueError(f"sensor desconhecido: {tag}")
        self.falhas_sensor[tag] = segundos
        self._corrente_falha[tag] = corrente_ma

    def forcar_falha(self, tag: str, segundos: float) -> None:
        """Provoca uma falha de propósito (útil em testes e exercícios)."""
        if tag not in FALHAS:
            raise ValueError(f"falha desconhecida: {tag}")
        self.falhas[tag] = segundos

    def passo(self, dt: float = 1.0) -> dict[str, float | bool]:
        self._atualizar_falhas(dt)

        aquecimento_ligado = not (FALHA_EXTRUSORA in self.falhas or EMERGENCIA in self.falhas)
        rosca_girando = aquecimento_ligado
        linha_andando = not self.falhas  # qualquer falha para o puxador

        # Temperaturas: aproximação exponencial (constante de tempo de 60 s)
        for tag, setpoint in SETPOINTS_TEMPERATURA.items():
            alvo = setpoint if aquecimento_ligado else TEMPERATURA_AMBIENTE
            tau = 60.0 if aquecimento_ligado else 600.0  # esfria bem mais devagar
            atual = self.temperaturas[tag]
            atual += (alvo - atual) * (1 - math.exp(-dt / tau))
            self.temperaturas[tag] = atual

        # Rotação e velocidade: rampa de 10 %/s do nominal
        self.rpm = _rampa(self.rpm, RPM_NOMINAL if rosca_girando else 0.0, 0.1 * RPM_NOMINAL * dt)
        self.velocidade = _rampa(
            self.velocidade, VELOCIDADE_NOMINAL if linha_andando else 0.0, 0.1 * VELOCIDADE_NOMINAL * dt
        )

        # Diâmetro: conservação de massa simplificada + deriva lenta.
        # Com a linha parada, o medidor continua vendo o último trecho de cabo.
        self.deriva_diametro += self._rng.gauss(0, 0.002 * math.sqrt(dt))
        self.deriva_diametro = max(-0.15, min(0.15, self.deriva_diametro))
        if self.velocidade > 1 and self.rpm > 1:
            relacao = (self.rpm / RPM_NOMINAL) / (self.velocidade / VELOCIDADE_NOMINAL)
            self.diametro = DIAMETRO_NOMINAL * math.sqrt(relacao) + self.deriva_diametro

        return {
            **{tag: t + self._rng.gauss(0, 0.3) for tag, t in self.temperaturas.items()},
            ROSCA_RPM: max(0.0, self.rpm + self._rng.gauss(0, 0.2) * (self.rpm > 0)),
            VELOCIDADE: max(0.0, self.velocidade + self._rng.gauss(0, 1.0) * (self.velocidade > 0)),
            DIAMETRO: max(0.0, self.diametro + self._rng.gauss(0, 0.01)),
            PRESSAO_MASSA: self._pressao(),
            FALHA_EXTRUSORA: FALHA_EXTRUSORA in self.falhas,
            FALHA_PUXADOR: FALHA_PUXADOR in self.falhas,
            EMERGENCIA: EMERGENCIA in self.falhas,
        }

    def _pressao(self) -> float:
        """Pressão sobe com a rotação e com material mais frio (mais viscoso)."""
        viscosidade = 1 + 0.01 * (SETPOINTS_TEMPERATURA[TEMP_CABECA] - self.temperaturas[TEMP_CABECA])
        pressao = PRESSAO_NOMINAL * (self.rpm / RPM_NOMINAL) * max(0.5, viscosidade)
        return max(0.0, pressao + self._rng.gauss(0, 2.0) * (self.rpm > 0))

    def _atualizar_falhas(self, dt: float) -> None:
        for ativas, catalogo in ((self.falhas, FALHAS), (self.falhas_sensor, FALHAS_SENSOR)):
            for tag in list(ativas):
                ativas[tag] -= dt
                if ativas[tag] <= 0:
                    del ativas[tag]
            if not self._falhas_aleatorias:
                continue
            for tag, (chance_por_s, (minimo, maximo)) in catalogo.items():
                if tag not in ativas and self._rng.random() < chance_por_s * dt:
                    ativas[tag] = self._rng.uniform(minimo, maximo)
        # Defeitos aleatórios de sensor são sempre fio rompido: esquece o curto já terminado
        for tag in list(self._corrente_falha):
            if tag not in self.falhas_sensor:
                del self._corrente_falha[tag]


def _rampa(atual: float, alvo: float, passo_maximo: float) -> float:
    if abs(alvo - atual) <= passo_maximo:
        return alvo
    return atual + math.copysign(passo_maximo, alvo - atual)
