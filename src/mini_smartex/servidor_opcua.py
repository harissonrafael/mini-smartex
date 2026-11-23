"""Servidor OPC-UA do "CLP": expõe as mesmas tags do Modbus, já em unidade de engenharia.

Muitos CLPs modernos (ex.: Siemens S7-1500) têm servidor OPC-UA embutido além
do Modbus/protocolo próprio. Aqui o simulador faz o mesmo: a cada passo do
processo, os registradores Modbus são montados e o servidor OPC-UA publica os
valores lidos dessa mesma memória, com StatusCode e timestamps.

Espaço de endereços:
    Objects/
      LinhaExtrusao01/                      (ns = urn:mini-smartex:extrusora)
        EXT01_TEMP_ZONA1   Double   °C      NodeId da coluna endereco_opcua
        ...
        LIN01_EMERGENCIA   Boolean
O NodeId de cada variável sai da coluna endereco_opcua do dicionário
(nsu=<URI>;s=<id>). Sem ela, vale nsu=urn:mini-smartex:extrusora;s=<tag>.
Cada variável tem as propriedades EngineeringUnits (unidade) e, quando o
dicionário tem faixa, EURange.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from asyncua import Server, ua

from mini_smartex import opcua_comum
from mini_smartex.dicionario import Tag

log = logging.getLogger("simulador.opcua")


class ServidorOpcua:
    def __init__(self, tags: list[Tag], endpoint: str = opcua_comum.ENDPOINT_PADRAO) -> None:
        self.tags = tags
        self.endpoint = endpoint
        self.server = Server()
        self._nos: dict[str, ua.NodeId] = {}
        self._tipos: dict[str, ua.VariantType] = {}

    async def iniciar(self) -> None:
        await self.server.init()
        self.server.set_endpoint(self.endpoint)
        self.server.set_server_name("mini-SMARTEX: CLP simulado da extrusora")
        # Sem criptografia nem login: só para estudo. Em produção, use
        # Basic256Sha256 + certificados (segurança é obrigatória na rede OT).
        self.server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

        idx = await self.server.register_namespace(opcua_comum.NAMESPACE_URI)
        # O "CLP" serve os nós exatamente onde o dicionário diz (coluna
        # endereco_opcua). Toda URI citada lá vira um namespace deste servidor.
        for uri in opcua_comum.uris_do_dicionario(self.tags):
            await self.server.register_namespace(uri)
        namespaces = await self.server.get_namespace_array()

        linha = await self.server.nodes.objects.add_object(idx, opcua_comum.NOME_OBJETO)
        for t in self.tags:
            booleano = t.tipo_bruto == "BOOL"
            tipo = ua.VariantType.Boolean if booleano else ua.VariantType.Double
            nodeid = opcua_comum.resolver_node_id(opcua_comum.endereco_opcua(t), namespaces)
            ns_no = nodeid.NamespaceIndex
            no = await linha.add_variable(nodeid, ua.QualifiedName(t.tag, ns_no), False if booleano else 0.0, tipo)
            await no.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(ua.LocalizedText(t.nome))))
            if t.unidade:
                unidade = ua.EUInformation(DisplayName=ua.LocalizedText(t.unidade), Description=ua.LocalizedText(t.unidade))
                await no.add_property(ns_no, "EngineeringUnits", unidade)
            faixa = t.faixa_instrumento or (
                (t.faixa_min, t.faixa_max) if t.faixa_min is not None and t.faixa_max is not None else None
            )
            if faixa and not booleano:
                await no.add_property(ns_no, "EURange", ua.Range(Low=faixa[0], High=faixa[1]))
            # Até o primeiro passo do processo não há dado: Bad, "esperando dado inicial".
            await self.server.write_attribute_value(
                no.nodeid,
                ua.DataValue(
                    ua.Variant(False if booleano else 0.0, tipo),
                    StatusCode=ua.StatusCode(ua.StatusCodes.BadWaitingForInitialData),
                ),
            )
            self._nos[t.tag] = no.nodeid
            self._tipos[t.tag] = tipo
        await self.server.start()
        log.info("Servidor OPC-UA em %s (namespaces: %s)", self.endpoint, ", ".join(
            f"{i}={uri}" for i, uri in enumerate(namespaces) if i > 0
        ))

    async def parar(self) -> None:
        await self.server.stop()

    async def publicar(self, registradores: dict[int, int], amostrado_em: datetime) -> None:
        """Publica um DataValue por tag, todos com o mesmo SourceTimestamp.

        `amostrado_em` é a hora, no relógio do CLP, em que a memória foi
        atualizada. O ServerTimestamp é a hora deste servidor no momento da
        publicação. Num CLP com servidor embutido os dois relógios são o mesmo;
        num gateway separado, não.
        """
        recebido_em = datetime.now(timezone.utc)
        for tag, (valor, status) in opcua_comum.valores_do_clp(self.tags, registradores).items():
            # Sensor com falha (StatusCode Bad): a especificação (Parte 4, §7.7.1)
            # manda o servidor entregar o valor nulo. O asyncua faria isso sozinho;
            # aqui fica explícito. O cliente recebe o status, a hora e nenhum número.
            variante = ua.Variant(None, ua.VariantType.Null) if valor is None else ua.Variant(valor, self._tipos[tag])
            await self.server.write_attribute_value(
                self._nos[tag],
                ua.DataValue(
                    variante,
                    StatusCode=ua.StatusCode(status),
                    SourceTimestamp=amostrado_em,
                    ServerTimestamp=recebido_em,
                ),
            )
