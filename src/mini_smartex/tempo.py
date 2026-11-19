"""Fuso horário único do projeto: simulador e leitor carimbam o tempo por aqui.

Para mudar o fuso dos timestamps (logs e telas), altere só `FUSO` abaixo.
Exemplo com horário de Brasília (no Windows precisa do pacote `tzdata`):

    from zoneinfo import ZoneInfo
    FUSO = ZoneInfo("America/Sao_Paulo")
"""

from __future__ import annotations

import logging
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

FUSO: tzinfo = ZoneInfo("America/Sao_Paulo")


def agora() -> datetime:
    """Data e hora atuais, já no fuso do projeto."""
    return datetime.now(FUSO)


class FormatadorLog(logging.Formatter):
    """Formatter de logging que escreve o `asctime` no fuso do projeto."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        momento = datetime.fromtimestamp(record.created, FUSO)
        if datefmt:
            return momento.strftime(datefmt)
        return f"{momento:%Y-%m-%d %H:%M:%S},{int(record.msecs):03d} {momento:%Z}"


def configurar_logging(nivel: int = logging.INFO) -> None:
    """Configura o logging raiz com timestamps no fuso do projeto."""
    manipulador = logging.StreamHandler()
    manipulador.setFormatter(FormatadorLog("%(asctime)s %(name)s %(message)s"))
    logging.basicConfig(level=nivel, handlers=[manipulador], force=True)
