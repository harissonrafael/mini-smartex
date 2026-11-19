"""Leitor: cliente Modbus TCP que lê o simulador e decodifica pelo dicionário.

É um ensaio do coletor que vem nos próximos dias. Por enquanto só lê e
mostra na tela.

Uso:
    uv run leitor                        # lê a cada 1 s
    uv run leitor --uma-vez              # lê uma vez e sai
    uv run leitor --ordem-forcada CDAB   # exercício: lê os FLOAT32 na ordem errada
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient

from mini_smartex import dicionario, mapa

DEVICE_ID = 1


def formatar(tags: list[dicionario.Tag], valores: dict[str, float | bool]) -> str:
    linhas = [f"{'tag':<22} {'valor':>12}  {'unidade':<6} faixa"]
    for t in tags:
        valor = valores[t.tag]
        if isinstance(valor, bool):
            texto = "LIGADO" if valor else "-"
            aviso = ""
        else:
            texto = f"{valor:.3f}" if t.unidade == "mm" else f"{valor:.1f}"
            fora = (t.faixa_min is not None and valor < t.faixa_min) or (
                t.faixa_max is not None and valor > t.faixa_max
            )
            aviso = "FORA DA FAIXA VÁLIDA" if fora else "ok"
        linhas.append(f"{t.tag:<22} {texto:>12}  {t.unidade:<6} {aviso}")
    return "\n".join(linhas)


async def executar(args: argparse.Namespace) -> None:
    caminho = Path(args.dicionario) if args.dicionario else dicionario.localizar_dicionario()
    tags = dicionario.carregar(caminho)
    inicio, quantidade = mapa.faixa_de_leitura(tags)

    client = AsyncModbusTcpClient(args.host, port=args.porta)
    await client.connect()
    if not client.connected:
        print("Não foi possível conectar. O simulador está rodando?")
        return
    try:
        while True:
            # Uma requisição só para todos os registradores (bloco contíguo)
            resposta = await client.read_holding_registers(inicio, count=quantidade, device_id=DEVICE_ID)
            ts = datetime.now(UTC)  # timestamp carimbado no coletor, em UTC
            if resposta.isError():
                print(f"[{ts:%H:%M:%S}Z] Erro na leitura: {resposta}")
            else:
                registradores = dict(enumerate(resposta.registers, start=inicio))
                valores = mapa.decodificar(tags, registradores, args.ordem_forcada)
                print(f"\n[{ts.isoformat(timespec='seconds')}] HR {inicio}..{inicio + quantidade - 1}")
                print(formatar(tags, valores))
            if args.uma_vez:
                break
            await asyncio.sleep(args.intervalo)
    finally:
        client.close()
        print("Servidor desconectato! O simulador ainda está rodando?")


def main() -> None:
    parser = argparse.ArgumentParser(description="Leitor Modbus do mini-SMARTEX")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--porta", type=int, default=5020)
    parser.add_argument("--intervalo", type=float, default=1.0)
    parser.add_argument("--uma-vez", action="store_true")
    parser.add_argument("--ordem-forcada", choices=["ABCD", "CDAB"], default=None)
    parser.add_argument("--dicionario", default=None)
    args = parser.parse_args()
    try:
        asyncio.run(executar(args))
    except KeyboardInterrupt:
        print("Leitor encerrado")


if __name__ == "__main__":
    main()
