"""Testes do OPC-UA (atividade de 23/11): StatusCode, timestamps e servidor real."""

import asyncio
import dataclasses
import socket
from datetime import datetime, timedelta, timezone

import pytest
from asyncua import Client, ua

from mini_smartex import mapa, opcua_comum
from mini_smartex.dicionario import Tag
from mini_smartex.servidor_opcua import ServidorOpcua

TEMP = Tag(
    tag="EXT01_TEMP_ZONA1", nome="Temperatura Zona 1", unidade="°C", tipo_bruto="FLOAT32", registrador=0,
    bit=None, ordem_palavras="ABCD", escala=1.0, offset=0.0, frequencia_s=5.0, faixa_min=0.0, faixa_max=350.0,
)
FALHA = Tag(
    tag="EXT01_FALHA", nome="Falha", unidade="", tipo_bruto="BOOL", registrador=20, bit=0,
    ordem_palavras="ABCD", escala=1.0, offset=0.0, frequencia_s=1.0, faixa_min=None, faixa_max=None,
)
PRESSAO = Tag(
    tag="EXT01_PRESSAO_MASSA", nome="Pressão", unidade="bar", tipo_bruto="AI_4_20MA", registrador=14,
    bit=None, ordem_palavras="ABCD", escala=1.0, offset=0.0, frequencia_s=1.0,
    faixa_min=0.0, faixa_max=500.0, faixa_instrumento=(0.0, 500.0),
)
TAGS = [TEMP, FALHA, PRESSAO]

# Para o teste com servidor real: a pressão vem de outro namespace, com NodeId
# no estilo Siemens S7-1500 (o identificador inclui as aspas), como se o
# dicionário tivesse a coluna endereco_opcua preenchida com o endereço real.
URI_S7 = "http://exemplo/simatic-s7-opcua"
PRESSAO_S7 = dataclasses.replace(PRESSAO, endereco_opcua=f'nsu={URI_S7};s="DB_Extrusora"."PressaoMassa"')
TAGS_SERVIDOR = [TEMP, FALHA, PRESSAO_S7]


def _memoria(pressao_ma: float | None = None) -> dict[int, int]:
    """Registradores com 180 °C, falha ligada e 250 bar (ou a corrente pedida)."""
    valores = {TEMP.tag: 180.0, FALHA.tag: True, PRESSAO.tag: 250.0}
    falhas = {PRESSAO.tag: pressao_ma} if pressao_ma is not None else None
    return mapa.montar_registradores(TAGS, valores, falhas)


# --- StatusCode ---


@pytest.mark.parametrize(
    "codigo, esperado",
    [
        (ua.StatusCodes.Good, "Good"),
        (ua.StatusCodes.UncertainEngineeringUnitsExceeded, "Uncertain"),
        (ua.StatusCodes.UncertainSensorNotAccurate, "Uncertain"),
        (ua.StatusCodes.BadSensorFailure, "Bad"),
        (ua.StatusCodes.BadWaitingForInitialData, "Bad"),
    ],
)
def test_severidade_pelos_dois_bits_mais_altos(codigo, esperado):
    assert opcua_comum.severidade(codigo) == esperado


def test_nome_status():
    assert opcua_comum.nome_status(ua.StatusCodes.BadSensorFailure) == "BadSensorFailure"


@pytest.mark.parametrize(
    "ma, status",
    [
        (None, ua.StatusCodes.Good),  # 250 bar = 12 mA
        (3.9, ua.StatusCodes.UncertainEngineeringUnitsExceeded),  # saturado abaixo de 4 mA
        (3.7, ua.StatusCodes.UncertainSensorNotAccurate),  # zona indefinida da NE43
        (0.0, ua.StatusCodes.BadSensorFailure),  # fio rompido
        (22.0, ua.StatusCodes.BadSensorFailure),  # curto
    ],
)
def test_status_da_pressao_segue_ne43(ma, status):
    valor, codigo = opcua_comum.valores_do_clp(TAGS, _memoria(ma))[PRESSAO.tag]
    assert codigo == status
    if status == ua.StatusCodes.BadSensorFailure:
        assert valor is None  # sem medição: o servidor não inventa um número


def test_tags_digitais_e_float_sao_good_e_iguais_ao_modbus():
    memoria = _memoria()
    clp = opcua_comum.valores_do_clp(TAGS, memoria)
    modbus = mapa.decodificar(TAGS, memoria)
    assert clp[TEMP.tag] == (pytest.approx(modbus[TEMP.tag]), ua.StatusCodes.Good)
    assert clp[FALHA.tag] == (True, ua.StatusCodes.Good)


# --- Timestamps ---


def test_desvio_do_relogio_da_fonte():
    agora = datetime.now(timezone.utc)
    adiantado = opcua_comum.relogio_da_fonte(90)
    assert adiantado.tzinfo is not None
    assert (adiantado - agora).total_seconds() == pytest.approx(90, abs=1)


def test_atraso_ms_aceita_datetime_sem_fuso_como_utc():
    fonte = datetime(2026, 11, 23, 14, 0, 0)  # o asyncua pode devolver assim (UTC implícito)
    servidor = datetime(2026, 11, 23, 14, 0, 0, 250_000, tzinfo=timezone.utc)
    assert opcua_comum.atraso_ms(fonte, servidor) == pytest.approx(250)
    assert opcua_comum.atraso_ms(servidor, fonte) == pytest.approx(-250)  # fonte adiantada
    assert opcua_comum.atraso_ms(None, servidor) is None


# --- Servidor de verdade, cliente de verdade (rede local) ---


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_servidor_publica_datavalue_completo():
    async def cenario():
        endpoint = f"opc.tcp://127.0.0.1:{_porta_livre()}/teste/"
        servidor = ServidorOpcua(TAGS_SERVIDOR, endpoint)
        await servidor.iniciar()
        try:
            amostra = datetime.now(timezone.utc) - timedelta(seconds=30)  # relógio do CLP 30 s atrasado
            await servidor.publicar(_memoria(), amostra)
            await servidor.publicar(_memoria(0.0), amostra)  # fio rompido na segunda amostra
            async with Client(endpoint) as client:
                namespaces = await client.get_namespace_array()
                enderecos = [opcua_comum.endereco_opcua(t) for t in TAGS_SERVIDOR]
                nos = [client.get_node(opcua_comum.resolver_node_id(e, namespaces)) for e in enderecos]
                return amostra, await client.read_attributes(nos)
        finally:
            await servidor.parar()

    amostra, (temp, falha, pressao) = asyncio.run(cenario())
    assert temp.Value.Value == pytest.approx(180.0)
    assert temp.StatusCode.is_good()
    assert falha.Value.Value is True
    assert pressao.StatusCode.value == ua.StatusCodes.BadSensorFailure
    assert pressao.Value.Value is None  # Bad: a especificação manda entregar valor nulo
    assert opcua_comum.para_utc(temp.SourceTimestamp) == amostra
    assert opcua_comum.atraso_ms(temp.SourceTimestamp, temp.ServerTimestamp) == pytest.approx(30_000, abs=2_000)


# --- Endereço OPC-UA no dicionário: URI do namespace, nunca o índice ---


def test_endereco_padrao_sem_coluna_no_dicionario():
    assert opcua_comum.endereco_opcua(TEMP) == "nsu=urn:mini-smartex:extrusora;s=EXT01_TEMP_ZONA1"
    assert opcua_comum.endereco_opcua(PRESSAO_S7) == f'nsu={URI_S7};s="DB_Extrusora"."PressaoMassa"'


def test_indice_segue_a_tabela_de_namespaces_do_servidor():
    endereco = f'nsu={URI_S7};s="DB_Extrusora"."PressaoMassa"'
    # Mesmo endereço, dois servidores (ou o mesmo reiniciado) com outra ordem:
    a = opcua_comum.resolver_node_id(endereco, ["http://opcfoundation.org/UA/", URI_S7])
    b = opcua_comum.resolver_node_id(endereco, ["http://opcfoundation.org/UA/", "urn:outro", URI_S7])
    assert (a.NamespaceIndex, b.NamespaceIndex) == (1, 2)
    assert a.Identifier == b.Identifier == '"DB_Extrusora"."PressaoMassa"'


def test_identificador_numerico():
    no = opcua_comum.resolver_node_id("nsu=urn:x;i=1042", ["http://opcfoundation.org/UA/", "urn:x"])
    assert no == ua.NodeId(1042, 1)


def test_namespace_que_o_servidor_nao_tem_e_erro_claro():
    with pytest.raises(opcua_comum.NamespaceDesconhecido, match="urn:nada"):
        opcua_comum.resolver_node_id("nsu=urn:nada;s=X", ["http://opcfoundation.org/UA/"])


def test_uris_do_dicionario_sem_repetir():
    assert opcua_comum.uris_do_dicionario(TAGS_SERVIDOR) == [opcua_comum.NAMESPACE_URI, URI_S7]
