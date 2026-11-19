"""Simulador da extrusora: servidor Modbus TCP que imita o CLP da linha.

Uso:
    uv run simulador                 # 127.0.0.1:5020, atualiza a cada 1 s
    uv run simulador --semente 42    # mesma sequência de valores toda vez
    uv run simulador --sem-falhas    # sem falhas aleatórias
    uv run simulador --fio-rompido 30  # pressão com fio rompido (0 mA) nos primeiros 30 s
    uv run simulador --curto 30        # pressão em curto (22 mA) nos primeiros 30 s

O mapa de registradores sai do docs/dicionario-de-dados.csv.

API do pymodbus usada (3.16): SimDevice/SimData para criar a memória do
dispositivo e `server.async_setValues` para atualizá-la. Desde a 3.13 o
`setValues` do datastore foi removido, e desde a 3.15 o contexto antigo
(ModbusServerContext) não é mais aceito.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from mini_smartex import dicionario, mapa, tempo
from mini_smartex.processo import (
    CORRENTE_CURTO_MA,
    CORRENTE_FIO_ROMPIDO_MA,
    PRESSAO_MASSA,
    TAGS_SIMULADAS,
    Extrusora,
)

log = logging.getLogger("simulador")

DEVICE_ID = 1
FC_HOLDING = 3  # código de função Modbus dos holding registers
TAMANHO_MEMORIA = 100  # holding registers 0..99


def criar_dispositivo() -> SimDevice:
    """Memória do "CLP": as 4 áreas do Modbus, cada uma separada.

    Ordem da tupla: (coils, discrete inputs, holding registers, input registers).
    Só usamos holding registers; as outras áreas existem porque o SimDevice pede.
    """
    coils = [SimData(0, count=10, values=False, datatype=DataType.BITS)]
    discrete_inputs = [SimData(0, count=10, values=False, datatype=DataType.BITS)]
    holding = [SimData(0, count=TAMANHO_MEMORIA, values=[0] * TAMANHO_MEMORIA, datatype=DataType.REGISTERS)]
    inputs = [SimData(0, count=10, values=[0] * 10, datatype=DataType.REGISTERS)]
    return SimDevice(DEVICE_ID, simdata=(coils, discrete_inputs, holding, inputs))


async def atualizar_registradores(
    server: ModbusTcpServer, tags: list[dicionario.Tag], extrusora: Extrusora, intervalo: float
) -> None:
    """Loop que avança o processo e escreve os registradores a cada `intervalo` s."""
    sem_modelo = [t.tag for t in tags if t.tag not in TAGS_SIMULADAS]
    if sem_modelo:
        log.warning("Tags do dicionário sem simulação (ficarão em 0): %s", ", ".join(sem_modelo))

    while True:
        valores = extrusora.passo(intervalo)
        registradores = mapa.montar_registradores(tags, valores, extrusora.sensores_com_falha)
        for inicio, bloco in mapa.agrupar_blocos(registradores):
            await server.async_setValues(DEVICE_ID, FC_HOLDING, inicio, bloco)

        falhas = ", ".join(sorted([*extrusora.falhas, *extrusora.falhas_sensor])) or "nenhuma"
        log.info(
            "Zona1=%.1f °C  RPM=%.1f  Vel=%.0f m/min  Diâm=%.3f mm  Pressão=%.0f bar  Falhas: %s",
            valores["EXT01_TEMP_ZONA1"],
            valores["EXT01_ROSCA_RPM"],
            valores["LIN01_VELOCIDADE"],
            valores["LIN01_DIAMETRO"],
            valores["EXT01_PRESSAO_MASSA"],
            falhas,
        )
        await asyncio.sleep(intervalo)


async def executar(args: argparse.Namespace) -> None:
    caminho = Path(args.dicionario) if args.dicionario else dicionario.localizar_dicionario()
    tags = dicionario.carregar(caminho)
    _, quantidade = mapa.faixa_de_leitura(tags)
    if max(t.registrador + t.quantidade_registradores for t in tags) > TAMANHO_MEMORIA:
        raise SystemExit(f"O dicionário usa registradores além de {TAMANHO_MEMORIA - 1}")
    log.info("Dicionário: %s (%d tags Modbus, %d registradores)", caminho, len(tags), quantidade)

    server = ModbusTcpServer(context=criar_dispositivo(), address=(args.host, args.porta))
    extrusora = Extrusora(semente=args.semente, falhas_aleatorias=not args.sem_falhas)
    if args.fio_rompido:
        extrusora.forcar_falha_sensor(PRESSAO_MASSA, args.fio_rompido, CORRENTE_FIO_ROMPIDO_MA)
        log.info("Fio do transmissor de pressão rompido nos primeiros %g s", args.fio_rompido)
    if args.curto:
        extrusora.forcar_falha_sensor(PRESSAO_MASSA, args.curto, CORRENTE_CURTO_MA)
        log.info("Curto no transmissor de pressão nos primeiros %g s", args.curto)
    log.info("Simulador Modbus em %s:%d (Ctrl+C para parar)", args.host, args.porta)

    # Servidor e loop de atualização rodam juntos; se um falhar, o outro para.
    async with asyncio.TaskGroup() as grupo:
        grupo.create_task(server.serve_forever())
        grupo.create_task(atualizar_registradores(server, tags, extrusora, args.intervalo))


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulador Modbus da extrusora (mini-SMARTEX)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--porta", type=int, default=5020)
    parser.add_argument("--intervalo", type=float, default=1.0, help="segundos entre atualizações")
    parser.add_argument("--semente", type=int, default=None, help="semente aleatória (reprodutível)")
    parser.add_argument("--sem-falhas", action="store_true", help="desliga falhas aleatórias")
    defeito = parser.add_mutually_exclusive_group()
    defeito.add_argument(
        "--fio-rompido", type=float, default=0, metavar="SEGUNDOS",
        help="simula fio rompido no transmissor de pressão (4–20 mA) no início",
    )
    defeito.add_argument(
        "--curto", type=float, default=0, metavar="SEGUNDOS",
        help="simula curto no transmissor de pressão (4–20 mA) no início",
    )
    parser.add_argument("--dicionario", default=None, help="caminho do dicionario-de-dados.csv")
    args = parser.parse_args()

    tempo.configurar_logging()
    try:
        asyncio.run(executar(args))
    except KeyboardInterrupt:
        print("Simulador encerrado")


if __name__ == "__main__":
    main()
