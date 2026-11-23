"""Partes do OPC-UA que não dependem de rede: nomes, StatusCode e timestamps.

Atividade de 23/11 (OPC-UA em produção). A diferença principal para o Modbus:

- Modbus entrega registradores crus. Quem lê precisa saber endereço, tipo,
  ordem de palavras e escala (por isso o dicionário).
- OPC-UA entrega um **DataValue** já em unidade de engenharia, com tipo, e com
  mais três campos que o Modbus não tem:
    * StatusCode: a qualidade do dado, dita pela própria fonte.
    * SourceTimestamp: quando a fonte (o CLP, o sensor) amostrou o valor.
    * ServerTimestamp: quando o servidor OPC-UA recebeu ou publicou o valor.

StatusCode é um inteiro de 32 bits. Os 2 bits mais altos dão a severidade:
    00 = Good       (0x0000_0000 ...)
    01 = Uncertain  (0x4000_0000 ...)
    10 = Bad        (0x8000_0000 ...)
O resto diz o motivo (sensor com falha, fora da faixa, sem comunicação...).
Regra prática no coletor: Good grava normal; Uncertain grava com flag de
qualidade; Bad grava a ocorrência, mas o valor não deve entrar em cálculo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from asyncua import ua

from mini_smartex import codec, mapa
from mini_smartex.dicionario import Tag

NAMESPACE_URI = "urn:mini-smartex:extrusora"
ENDPOINT_PADRAO = "opc.tcp://127.0.0.1:4840/mini-smartex/"
NOME_OBJETO = "LinhaExtrusao01"  # objeto que agrupa as variáveis da linha

GOOD = "Good"
UNCERTAIN = "Uncertain"
BAD = "Bad"

# Diagnóstico NE43 do sinal 4–20 mA -> StatusCode que um CLP publicaria.
STATUS_POR_DIAGNOSTICO = {
    codec.DIAG_OK: ua.StatusCodes.Good,
    codec.DIAG_SATURADO: ua.StatusCodes.UncertainEngineeringUnitsExceeded,
    codec.DIAG_INDEFINIDO: ua.StatusCodes.UncertainSensorNotAccurate,
    codec.DIAG_FALHA_BAIXA: ua.StatusCodes.BadSensorFailure,
    codec.DIAG_FALHA_ALTA: ua.StatusCodes.BadSensorFailure,
}


class NamespaceDesconhecido(LookupError):
    """O dicionário cita uma URI de namespace que o servidor não tem."""


def endereco_opcua(tag: Tag) -> str:
    """Endereço da tag no formato do dicionário: nsu=<URI>;s=<id>.

    Sem a coluna endereco_opcua preenchida, vale o padrão do simulador:
    a própria tag no namespace do mini-SMARTEX.
    """
    return tag.endereco_opcua or f"nsu={NAMESPACE_URI};s={tag.tag}"


def uris_do_dicionario(tags: list[Tag]) -> list[str]:
    """URIs de namespace citadas pelas tags, sem repetir, na ordem em que aparecem."""
    uris: list[str] = []
    for t in tags:
        uri = ua.NodeId.from_string(endereco_opcua(t)).NamespaceUri
        if uri not in uris:
            uris.append(uri)
    return uris


def resolver_node_id(endereco: str, namespaces: list[str]) -> ua.NodeId:
    """'nsu=<URI>;s=<id>' -> NodeId com o índice que a URI tem *neste* servidor.

    `namespaces` é a tabela de namespaces do servidor (client.get_namespace_array()):
    o índice é a posição da URI nela. Por isso o dicionário guarda a URI, e o
    coletor resolve o índice a cada conexão.
    """
    expandido = ua.NodeId.from_string(endereco)  # vem como ExpandedNodeId, com NamespaceIndex 0
    uri = expandido.NamespaceUri
    if uri not in namespaces:
        raise NamespaceDesconhecido(
            f"o servidor não tem o namespace {uri!r} (citado em {endereco!r}). "
            f"Namespaces do servidor: {', '.join(namespaces)}"
        )
    return ua.NodeId(expandido.Identifier, namespaces.index(uri), expandido.NodeIdType)


def severidade(codigo: int) -> str:
    """Good, Uncertain ou Bad, pelos 2 bits mais altos do StatusCode."""
    bits = (codigo >> 30) & 0b11
    return {0: GOOD, 1: UNCERTAIN}.get(bits, BAD)


def nome_status(codigo: int) -> str:
    """0x808B0000 -> 'BadSensorFailure'."""
    return ua.StatusCode(codigo).name


def valores_do_clp(
    tags: list[Tag], registradores: dict[int, int]
) -> dict[str, tuple[float | bool | None, int]]:
    """{tag: (valor de engenharia, StatusCode)} a partir da memória do CLP.

    O servidor OPC-UA do CLP lê a própria memória (os mesmos registradores que
    o Modbus expõe), então as duas interfaces entregam sempre o mesmo valor.
    Sinais 4–20 mA com falha voltam com valor None e StatusCode Bad.
    """
    valores = mapa.decodificar(tags, registradores)
    diagnosticos = mapa.diagnosticar(tags, registradores)
    resultado: dict[str, tuple[float | bool | None, int]] = {}
    for t in tags:
        if t.tag in diagnosticos:
            _, diag = diagnosticos[t.tag]
            resultado[t.tag] = (valores[t.tag], STATUS_POR_DIAGNOSTICO[diag])
        else:
            resultado[t.tag] = (valores[t.tag], ua.StatusCodes.Good)
    return resultado


def relogio_da_fonte(desvio_s: float = 0.0) -> datetime:
    """Hora do relógio do "CLP", em UTC (OPC-UA trafega sempre em UTC).

    `desvio_s` simula um CLP sem NTP, com o relógio adiantado (+) ou
    atrasado (−). É o que torna o SourceTimestamp pouco confiável.
    """
    return datetime.now(timezone.utc) + timedelta(seconds=desvio_s)


def atraso_ms(fonte: datetime | None, servidor: datetime | None) -> float | None:
    """ServerTimestamp − SourceTimestamp, em ms. Negativo = relógio da fonte adiantado."""
    if fonte is None or servidor is None:
        return None
    return (para_utc(servidor) - para_utc(fonte)).total_seconds() * 1000


def para_utc(momento: datetime) -> datetime:
    """O asyncua pode devolver datetime sem fuso; nesse caso ele está em UTC."""
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)
