"""Leitor OPC-UA: lê as tags do simulador e mostra StatusCode e os dois timestamps.

Uso:
    uv run leitor-opcua                 # polling: lê tudo a cada 1 s
    uv run leitor-opcua --uma-vez       # lê uma vez e sai
    uv run leitor-opcua --assinatura    # subscription: o servidor avisa quando muda
    uv run leitor-opcua --reconexao 2   # tenta reconectar a cada 2 s (padrão: 5 s)

Compare com o leitor Modbus: aqui não há endereço, escala nem ordem de
palavras. O servidor já entrega o valor em unidade de engenharia, com tipo,
qualidade (StatusCode) e hora da amostra (SourceTimestamp).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from asyncua import Client, ua

from mini_smartex import dicionario, opcua_comum, tempo

INTERVALO_RECONEXAO_S = 5.0
PERIODO_ASSINATURA_MS = 500  # de quanto em quanto tempo o servidor verifica mudanças


def _hora(momento: datetime | None) -> str:
    """Timestamp OPC-UA (UTC) mostrado no fuso do projeto."""
    if momento is None:
        return "—"
    return opcua_comum.para_utc(momento).astimezone(tempo.FUSO).strftime("%H:%M:%S.%f")[:-3]


def formatar_valor(tag: dicionario.Tag, dv: ua.DataValue) -> str:
    """Valor para a tela. Com StatusCode Bad o valor não vale nada: mostra '—'."""
    if opcua_comum.severidade(dv.StatusCode.value) == opcua_comum.BAD:
        return "—"
    valor = dv.Value.Value
    if isinstance(valor, bool):
        return "LIGADO" if valor else "-"
    return f"{valor:.3f}" if tag.unidade == "mm" else f"{valor:.1f}"


def formatar(tags: list[dicionario.Tag], valores: list[ua.DataValue]) -> str:
    linhas = [
        f"{'tag':<22} {'valor':>10}  {'unid.':<6} {'status':<34} {'fonte':<12} {'servidor':<12} Δ ms"
    ]
    for t, dv in zip(tags, valores):
        atraso = opcua_comum.atraso_ms(dv.SourceTimestamp, dv.ServerTimestamp)
        linhas.append(
            f"{t.tag:<22} {formatar_valor(t, dv):>10}  {t.unidade:<6} "
            f"{opcua_comum.nome_status(dv.StatusCode.value):<34} "
            f"{_hora(dv.SourceTimestamp):<12} {_hora(dv.ServerTimestamp):<12} "
            f"{'—' if atraso is None else f'{atraso:.0f}'}"
        )
    return "\n".join(linhas)


class Notificacoes:
    """Recebe as notificações da assinatura. O asyncua chama `datachange_notification`.

    Por padrão o servidor só notifica quando o valor ou o StatusCode mudam
    (DataChangeTrigger StatusValue). Um bit que fica em 0 não gera tráfego.
    """

    def __init__(self, tags_por_no: dict[ua.NodeId, dicionario.Tag]) -> None:
        self.tags_por_no = tags_por_no

    def datachange_notification(self, node, val, data) -> None:  # noqa: ANN001 (assinatura do asyncua)
        t = self.tags_por_no[node.nodeid]
        dv: ua.DataValue = data.monitored_item.Value
        print(
            f"[{_hora(dv.SourceTimestamp)}] {t.tag:<22} {formatar_valor(t, dv):>10} {t.unidade:<6} "
            f"{opcua_comum.nome_status(dv.StatusCode.value)}"
        )

    def status_change_notification(self, status) -> None:  # noqa: ANN001
        """A própria assinatura mudou de estado (ex.: servidor parou de responder)."""
        print(f"[{tempo.agora():%H:%M:%S}] Estado da assinatura: {opcua_comum.nome_status(status.Status.value)}")


async def conectar(client: Client, espera: float, insistir: bool) -> bool:
    """Conecta ao servidor. Com `insistir`, tenta de novo a cada `espera` segundos."""
    while True:
        try:
            await client.connect()
            print("Conectado ao servidor OPC-UA.")
            return True
        except (OSError, asyncio.TimeoutError, ua.UaError):
            pass
        if not insistir:
            return False
        print(f"Sem conexão com o servidor OPC-UA. Nova tentativa em {espera:g} s...")
        await asyncio.sleep(espera)


async def sessao(client: Client, tags: list[dicionario.Tag], args: argparse.Namespace) -> None:
    """Uma sessão conectada: lê (ou assina) até a conexão cair ou terminar."""
    # O índice do namespace é descoberto pela URI a cada conexão: nunca fixe "ns=2".
    idx = await client.get_namespace_index(opcua_comum.NAMESPACE_URI)
    nos = [client.get_node(opcua_comum.node_id(t.tag, idx)) for t in tags]

    if args.assinatura:
        handler = Notificacoes({no.nodeid: t for no, t in zip(nos, tags)})
        assinatura = await client.create_subscription(PERIODO_ASSINATURA_MS, handler)
        await assinatura.subscribe_data_change(nos)
        print("Assinatura criada. Aguardando mudanças (Ctrl+C para sair)...")
        while True:
            await asyncio.sleep(1)
            await client.check_connection()  # levanta exceção se a conexão caiu

    while True:
        # Uma requisição Read para todos os nós, como o bloco contíguo do Modbus.
        valores = await client.read_attributes(nos)
        print(f"\n[{tempo.agora().isoformat(timespec='seconds')}] {len(nos)} nós lidos (ns={idx})")
        print(formatar(tags, valores))
        if args.uma_vez:
            return
        await asyncio.sleep(args.intervalo)


async def executar(args: argparse.Namespace) -> None:
    caminho = Path(args.dicionario) if args.dicionario else dicionario.localizar_dicionario()
    tags = dicionario.carregar(caminho)

    while True:
        client = Client(url=args.endpoint, timeout=4)
        if not await conectar(client, args.reconexao, insistir=not args.uma_vez):
            print("Não foi possível conectar. O simulador está rodando (sem --sem-opcua)?")
            return
        try:
            await sessao(client, tags, args)
            return  # --uma-vez
        except (OSError, asyncio.TimeoutError, ua.UaError, ConnectionError) as erro:
            print(f"[{tempo.agora():%H:%M:%S %Z}] Conexão perdida com o servidor OPC-UA ({type(erro).__name__}).")
            if args.uma_vez:
                return
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 (a conexão já pode estar morta)
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Leitor OPC-UA do mini-SMARTEX")
    parser.add_argument("--endpoint", default=opcua_comum.ENDPOINT_PADRAO)
    parser.add_argument("--intervalo", type=float, default=1.0)
    parser.add_argument("--reconexao", type=float, default=INTERVALO_RECONEXAO_S)
    parser.add_argument("--uma-vez", action="store_true")
    parser.add_argument("--assinatura", action="store_true", help="usa subscription em vez de polling")
    parser.add_argument("--dicionario", default=None)
    args = parser.parse_args()
    # Quando o servidor cai, o asyncua registra tracebacks internos; o leitor já
    # avisa a queda com uma linha e reconecta, então eles só poluiriam a tela.
    logging.getLogger("asyncua").setLevel(logging.CRITICAL)
    try:
        asyncio.run(executar(args))
    except KeyboardInterrupt:
        print("Leitor OPC-UA encerrado")


if __name__ == "__main__":
    main()
