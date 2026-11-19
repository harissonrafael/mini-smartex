"""Leitor: cliente Modbus TCP que lê o simulador e decodifica pelo dicionário.

É um ensaio do coletor que vem nos próximos dias. Por enquanto só lê e
mostra na tela.

Uso:
    uv run leitor                        # lê a cada 1 s
    uv run leitor --uma-vez              # lê uma vez e sai
    uv run leitor --ordem-forcada CDAB   # exercício: lê os FLOAT32 na ordem errada
    uv run leitor --reconexao 2          # tenta reconectar a cada 2 s (padrão: 5 s)

Se o servidor cair, o leitor tenta reconectar sozinho (exceto com --uma-vez).
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from mini_smartex import dicionario, mapa, tempo

DEVICE_ID = 1
# Tempo de espera entre tentativas de (re)conexão com o servidor, em segundos.
# Para mudar o padrão, altere aqui; para um teste pontual, use --reconexao.
INTERVALO_RECONEXAO_S = 5.0


def formatar(
    tags: list[dicionario.Tag],
    valores: dict[str, float | bool | None],
    diagnosticos: dict[str, tuple[float, str]] | None = None,
) -> str:
    diagnosticos = diagnosticos or {}
    linhas = [f"{'tag':<22} {'valor':>12}  {'unidade':<6} qualidade"]
    for t in tags:
        valor = valores[t.tag]
        if isinstance(valor, bool):
            texto = "LIGADO" if valor else "-"
            aviso = ""
        elif valor is None:
            texto = "—"
            aviso = ""
        else:
            texto = f"{valor:.3f}" if t.unidade == "mm" else f"{valor:.1f}"
            fora = (t.faixa_min is not None and valor < t.faixa_min) or (
                t.faixa_max is not None and valor > t.faixa_max
            )
            aviso = "FORA DA FAIXA VÁLIDA" if fora else "ok"
        if t.tag in diagnosticos:  # sinal 4–20 mA: mostra a corrente e o diagnóstico NE43
            ma, diag = diagnosticos[t.tag]
            aviso = f"{ma:6.2f} mA  {diag.upper() if diag != 'ok' else aviso}"
        linhas.append(f"{t.tag:<22} {texto:>12}  {t.unidade:<6} {aviso}")
    return "\n".join(linhas)


async def conectar(client: AsyncModbusTcpClient, espera: float, insistir: bool) -> bool:
    """Conecta ao servidor. Com `insistir`, tenta de novo a cada `espera` segundos."""
    while True:
        try:
            await client.connect()
        except OSError:
            pass
        if client.connected:
            print("Conectado ao servidor.")
            return True
        if not insistir:
            return False
        print(f"Sem conexão com o servidor. Nova tentativa em {espera:g} s...")
        await asyncio.sleep(espera)


async def executar(args: argparse.Namespace) -> None:
    caminho = Path(args.dicionario) if args.dicionario else dicionario.localizar_dicionario()
    tags = dicionario.carregar(caminho)
    inicio, quantidade = mapa.faixa_de_leitura(tags)

    client = AsyncModbusTcpClient(args.host, port=args.porta)
    # Com --uma-vez não vale a pena insistir: tenta conectar uma única vez
    if not await conectar(client, args.reconexao, insistir=not args.uma_vez):
        print("Não foi possível conectar. O simulador está rodando?")
        return
    try:
        while True:
            # Uma requisição só para todos os registradores (bloco contíguo)
            try:
                resposta = await client.read_holding_registers(
                    inicio, count=quantidade, device_id=DEVICE_ID
                )
            except (ModbusException, OSError) as erro:
                resposta = erro
            ts = tempo.agora()  # timestamp carimbado no coletor, no fuso de tempo.FUSO
            if not client.connected:
                print(f"[{ts:%H:%M:%S %Z}] Conexão perdida com o servidor.")
                if args.uma_vez:
                    break
                client.close()
                await conectar(client, args.reconexao, insistir=True)
                continue
            if isinstance(resposta, Exception) or resposta.isError():
                print(f"[{ts:%H:%M:%S %Z}] Erro na leitura: {resposta}")
            else:
                registradores = dict(enumerate(resposta.registers, start=inicio))
                valores = mapa.decodificar(tags, registradores, args.ordem_forcada)
                print(f"\n[{ts.isoformat(timespec='seconds')}] HR {inicio}..{inicio + quantidade - 1}")
                print(formatar(tags, valores, mapa.diagnosticar(tags, registradores)))
            if args.uma_vez:
                break
            await asyncio.sleep(args.intervalo)
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Leitor Modbus do mini-SMARTEX")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--porta", type=int, default=5020)
    parser.add_argument("--intervalo", type=float, default=1.0)
    parser.add_argument(
        "--reconexao",
        type=float,
        default=INTERVALO_RECONEXAO_S,
        help="segundos entre tentativas de reconexão (padrão: %(default)s)",
    )
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
