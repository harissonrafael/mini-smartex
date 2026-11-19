"""Carrega o dicionário de dados (docs/dicionario-de-dados.csv).

O dicionário é a fonte única da verdade: o simulador usa este arquivo para
saber quais registradores criar, e o leitor usa o mesmo arquivo para saber
como decodificar o que leu. Mudou o dicionário, mudam os dois.

Só as linhas com protocolo Modbus entram aqui. As do FINDER e as variáveis
derivadas ficam para as próximas etapas do pipeline.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

NOME_ARQUIVO = "dicionario-de-dados.csv"

# Aceita "HR 0–1", "HR 0-1", "HR 10" e "HR 20 bit 2".
# HR = holding register. O traço pode ser "–" (en dash) ou "-".
_RE_ENDERECO = re.compile(
    r"^HR\s+(\d+)(?:\s*[–-]\s*(\d+))?(?:\s+bit\s+(\d+))?$", re.IGNORECASE
)


@dataclass(frozen=True)
class Tag:
    """Uma variável lida do CLP, como descrita no dicionário."""

    tag: str
    nome: str
    unidade: str
    tipo_bruto: str  # "FLOAT32", "UINT16" ou "BOOL"
    registrador: int  # primeiro holding register
    bit: int | None  # só para BOOL
    ordem_palavras: str  # "ABCD" ou "CDAB" (só para FLOAT32)
    escala: float
    offset: float
    frequencia_s: float | None
    faixa_min: float | None
    faixa_max: float | None

    @property
    def quantidade_registradores(self) -> int:
        """FLOAT32 ocupa 2 registradores de 16 bits; o resto ocupa 1."""
        return 2 if self.tipo_bruto == "FLOAT32" else 1


def localizar_dicionario(inicio: Path | None = None) -> Path:
    """Sobe as pastas a partir de `inicio` até achar docs/dicionario-de-dados.csv.

    Assim o projeto funciona dentro de "MPT - SMARTEX/workspaces/python/..."
    sem caminho fixo no código.
    """
    pasta = (inicio or Path.cwd()).resolve()
    for candidata in (pasta, *pasta.parents):
        caminho = candidata / "docs" / NOME_ARQUIVO
        if caminho.is_file():
            return caminho
    raise FileNotFoundError(
        f"Não achei docs/{NOME_ARQUIVO} subindo a partir de {pasta}. "
        "Use --dicionario para informar o caminho."
    )


def carregar(caminho: Path) -> list[Tag]:
    """Lê o CSV (separado por ';', UTF-8 com BOM) e devolve as tags Modbus."""
    tags: list[Tag] = []
    with open(caminho, encoding="utf-8-sig", newline="") as arquivo:
        for linha in csv.DictReader(arquivo, delimiter=";"):
            if not linha["protocolo"].strip().lower().startswith("modbus"):
                continue  # FINDER e derivados não vêm do CLP
            tags.append(_linha_para_tag(linha))
    _validar_sobreposicao(tags)
    return tags


def _linha_para_tag(linha: dict[str, str]) -> Tag:
    nome_tag = linha["tag"].strip()
    tipo = _tipo(linha["tipo_bruto"])

    achado = _RE_ENDERECO.match(linha["endereco"].strip())
    if not achado:
        raise ValueError(f"{nome_tag}: endereço inválido: {linha['endereco']!r}")
    inicio = int(achado.group(1))
    fim = int(achado.group(2)) if achado.group(2) else inicio
    bit = int(achado.group(3)) if achado.group(3) else None

    if tipo == "FLOAT32" and fim != inicio + 1:
        raise ValueError(f"{nome_tag}: FLOAT32 precisa de 2 registradores (ex.: HR 0–1)")
    if tipo == "BOOL" and bit is None:
        raise ValueError(f"{nome_tag}: BOOL precisa do bit (ex.: HR 20 bit 0)")
    if bit is not None and not 0 <= bit <= 15:
        raise ValueError(f"{nome_tag}: bit fora de 0..15")

    return Tag(
        tag=nome_tag,
        nome=linha["nome"].strip(),
        unidade=_texto_ou_vazio(linha["unidade"]),
        tipo_bruto=tipo,
        registrador=inicio,
        bit=bit,
        ordem_palavras="CDAB" if "CDAB" in linha["ordem_palavras"].upper() else "ABCD",
        escala=_numero(linha["escala"]) or 1.0,
        offset=_numero(linha["offset"]) or 0.0,
        frequencia_s=_frequencia(linha["frequencia"]),
        faixa_min=_numero(linha["faixa_valida_min"]),
        faixa_max=_numero(linha["faixa_valida_max"]),
    )


def _validar_sobreposicao(tags: list[Tag]) -> None:
    """Dois valores não podem ocupar o mesmo registrador (exceto bits diferentes)."""
    ocupado: dict[int, str] = {}
    bits: dict[tuple[int, int], str] = {}
    for t in tags:
        if t.tipo_bruto == "BOOL":
            chave = (t.registrador, t.bit)
            if chave in bits:
                raise ValueError(f"{t.tag} e {bits[chave]} usam o mesmo bit")
            bits[chave] = t.tag
            continue
        for r in range(t.registrador, t.registrador + t.quantidade_registradores):
            if r in ocupado:
                raise ValueError(f"{t.tag} e {ocupado[r]} usam o registrador {r}")
            ocupado[r] = t.tag
    for (r, _), nome in bits.items():
        if r in ocupado:
            raise ValueError(f"{nome} usa o registrador {r}, que já é de {ocupado[r]}")


def _tipo(texto: str) -> str:
    texto = texto.upper()
    for tipo in ("FLOAT32", "UINT16", "BOOL"):
        if tipo in texto:
            return tipo
    raise ValueError(f"tipo_bruto não suportado: {texto!r}")


def _texto_ou_vazio(texto: str) -> str:
    texto = texto.strip()
    return "" if texto in ("—", "-") else texto


def _numero(texto: str) -> float | None:
    """Converte número no formato brasileiro ("0,1") e ignora "—" e textos."""
    texto = texto.strip().replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _frequencia(texto: str) -> float | None:
    """ "5 s" -> 5.0. Frequências por evento ("por lote") viram None."""
    achado = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*s\s*$", texto)
    return float(achado.group(1).replace(",", ".")) if achado else None
