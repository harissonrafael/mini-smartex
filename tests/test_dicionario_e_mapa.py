from pathlib import Path

import pytest

from mini_smartex import dicionario, mapa
from mini_smartex.processo import TAGS_SIMULADAS, Extrusora

CABECALHO = (
    "tag;nome;descricao;origem;protocolo;endereco;tipo_bruto;ordem_palavras;escala;offset;"
    "unidade;tipo_destino;frequencia;faixa_valida_min;faixa_valida_max;limite_alerta_min;"
    "limite_alerta_max;regra_transformacao;regras_qualidade;tabela_destino;status;observacoes"
)


def _csv(tmp_path: Path, *linhas: str) -> Path:
    caminho = tmp_path / "dicionario-de-dados.csv"
    caminho.write_text("\n".join([CABECALHO, *linhas]), encoding="utf-8-sig")
    return caminho


def _linha(tag, endereco, tipo, escala="1", ordem="big-endian (ABCD)", protocolo="Modbus TCP"):
    return f"{tag};{tag};;CLP;{protocolo};{endereco};{tipo};{ordem};{escala};0;°C;;1 s;0;350;;;;;;;"


def test_le_tipos_e_enderecos(tmp_path):
    caminho = _csv(
        tmp_path,
        _linha("T1", "HR 0–1", "FLOAT32"),
        _linha("RPM", "HR 10", "UINT16", escala="0,1"),
        _linha("F", "HR 20 bit 2", "BOOL (bit)"),
        _linha("LOTE", "tabela lote.id", "TEXT", protocolo="Banco/API"),
    )
    tags = {t.tag: t for t in dicionario.carregar(caminho)}
    assert set(tags) == {"T1", "RPM", "F"}  # FINDER fica de fora
    assert tags["T1"].registrador == 0 and tags["T1"].quantidade_registradores == 2
    assert tags["RPM"].escala == pytest.approx(0.1)
    assert tags["F"].registrador == 20 and tags["F"].bit == 2


def test_float32_com_um_registrador_so_e_erro(tmp_path):
    with pytest.raises(ValueError, match="2 registradores"):
        dicionario.carregar(_csv(tmp_path, _linha("T1", "HR 0", "FLOAT32")))


def test_sobreposicao_e_erro(tmp_path):
    caminho = _csv(tmp_path, _linha("A", "HR 0–1", "FLOAT32"), _linha("B", "HR 1", "UINT16"))
    with pytest.raises(ValueError, match="registrador 1"):
        dicionario.carregar(caminho)


def test_dicionario_real_do_projeto():
    """Lê o docs/dicionario-de-dados.csv de verdade, se estiver nas pastas acima."""
    try:
        caminho = dicionario.localizar_dicionario(Path(__file__).parent)
    except FileNotFoundError:
        pytest.skip("docs/dicionario-de-dados.csv não encontrado")
    tags = dicionario.carregar(caminho)
    assert {t.tag for t in tags} <= set(TAGS_SIMULADAS)
    assert mapa.faixa_de_leitura(tags) == (0, 21)


def test_ida_e_volta_simulador_leitor(tmp_path):
    """O que o simulador escreve, o leitor decodifica igual."""
    caminho = _csv(
        tmp_path,
        _linha("EXT01_TEMP_ZONA1", "HR 0–1", "FLOAT32"),
        _linha("EXT01_ROSCA_RPM", "HR 10", "UINT16", escala="0,1"),
        _linha("EXT01_FALHA", "HR 20 bit 0", "BOOL (bit)"),
        _linha("LIN01_EMERGENCIA", "HR 20 bit 2", "BOOL (bit)"),
    )
    tags = dicionario.carregar(caminho)
    extrusora = Extrusora(semente=1, falhas_aleatorias=False)
    extrusora.forcar_falha("LIN01_EMERGENCIA", 5)
    valores = extrusora.passo()

    registradores = mapa.montar_registradores(tags, valores)
    lidos = mapa.decodificar(tags, registradores)

    assert lidos["EXT01_TEMP_ZONA1"] == pytest.approx(valores["EXT01_TEMP_ZONA1"], rel=1e-6)
    assert lidos["EXT01_ROSCA_RPM"] == pytest.approx(valores["EXT01_ROSCA_RPM"], abs=0.05)
    assert lidos["EXT01_FALHA"] is False
    assert lidos["LIN01_EMERGENCIA"] is True
    assert registradores[20] == 0b100


def test_agrupar_blocos():
    assert mapa.agrupar_blocos({0: 1, 1: 2, 5: 3, 6: 4, 20: 5}) == [(0, [1, 2]), (5, [3, 4]), (20, [5])]


# --- Coluna endereco_opcua (exercício 6 de 23/11) ---


def _csv_opcua(tmp_path: Path, *pares: tuple[str, str]) -> Path:
    """CSV com a coluna endereco_opcua no fim.

    O NodeId tem ';' (o separador do CSV), então vai entre aspas, e as aspas do
    próprio valor (NodeIds da Siemens) são dobradas. O Excel faz isso sozinho.
    """
    linhas = [
        f'{_linha(tag, f"HR {i * 2}–{i * 2 + 1}", "FLOAT32")};"{no.replace(chr(34), chr(34) * 2)}"'
        for i, (tag, no) in enumerate(pares)
    ]
    caminho = tmp_path / "dicionario-de-dados.csv"
    caminho.write_text("\n".join([CABECALHO + ";endereco_opcua", *linhas]), encoding="utf-8-sig")
    return caminho


def test_le_endereco_opcua(tmp_path):
    caminho = _csv_opcua(tmp_path, ("T1", 'nsu=http://exemplo/s7;s="DB_Extrusora"."TempZona1"'), ("T2", "nsu=urn:x;i=7"))
    tags = {t.tag: t for t in dicionario.carregar(caminho)}
    assert tags["T1"].endereco_opcua == 'nsu=http://exemplo/s7;s="DB_Extrusora"."TempZona1"'
    assert tags["T2"].endereco_opcua == "nsu=urn:x;i=7"


def test_sem_coluna_endereco_opcua_fica_none(tmp_path):
    assert dicionario.carregar(_csv(tmp_path, _linha("T1", "HR 0–1", "FLOAT32")))[0].endereco_opcua is None


def test_indice_de_namespace_fixo_e_recusado(tmp_path):
    with pytest.raises(ValueError, match="índice fixo"):
        dicionario.carregar(_csv_opcua(tmp_path, ("T1", "ns=2;s=T1")))


def test_endereco_opcua_malformado_e_erro(tmp_path):
    with pytest.raises(ValueError, match="endereco_opcua inválido"):
        dicionario.carregar(_csv_opcua(tmp_path, ("T1", "nsu=urn:x;x=T1")))


def test_endereco_opcua_repetido_e_erro(tmp_path):
    with pytest.raises(ValueError, match="mesmo endereco_opcua"):
        dicionario.carregar(_csv_opcua(tmp_path, ("A", "nsu=urn:x;s=A"), ("B", "nsu=urn:x;s=A")))


def test_dicionario_real_tem_endereco_opcua_em_todas_as_tags_modbus():
    tags = dicionario.carregar(dicionario.localizar_dicionario(Path(__file__).parent))
    assert all(t.endereco_opcua and t.endereco_opcua.startswith("nsu=") for t in tags)
