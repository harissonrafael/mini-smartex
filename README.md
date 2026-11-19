# mini-SMARTEX

Versão de estudo, em escala reduzida, do pipeline de dados do projeto SMARTEX (extrusão de cabos, MPT Cable). O plano completo está em `../../../docs/plano-de-estudos.md`, seção 8.

Etapa atual: **simulador da extrusora v1** (cronograma de 19/11).

## Como rodar

Requisitos: Python 3.13+ e [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

Terminal 1, o "CLP" simulado:

```bash
uv run simulador                  # Modbus TCP em 127.0.0.1:5020, atualiza a cada 1 s
uv run simulador --semente 42     # mesma sequência de valores toda vez
uv run simulador --sem-falhas     # desliga falhas aleatórias
```

Terminal 2, o leitor:

```bash
uv run leitor                     # lê e decodifica a cada 1 s
uv run leitor --uma-vez           # lê uma vez e sai
uv run leitor --ordem-forcada CDAB  # exercício: lê os FLOAT32 na ordem errada
```

Testes (não precisam do simulador rodando):

```bash
uv run pytest
```

## Como funciona

```
docs/dicionario-de-dados.csv  ──►  dicionario.py  (lê as tags Modbus)
                                        │
             ┌──────────────────────────┴──────────────────────────┐
             ▼                                                     ▼
processo.py (modelo da extrusora)                         leitor.py (cliente Modbus)
             │  valores de engenharia                              ▲  registradores
             ▼                                                     │
mapa.py + codec.py  ──►  registradores  ──►  simulador.py  ──Modbus TCP──┘
                         (float32, uint16      (servidor, pymodbus)
                          escalado, bits)
```

| Arquivo | Papel |
|---|---|
| `dicionario.py` | Lê o CSV do dicionário. Valida endereços e sobreposição de registradores |
| `codec.py` | Converte valores ↔ registradores de 16 bits: float32 (ABCD/CDAB), inteiro escalado e bits. Feito à mão com `struct`, para entender o que acontece por baixo |
| `mapa.py` | Monta todos os registradores a partir das tags (simulador) e decodifica de volta (leitor) |
| `processo.py` | Modelo simples da linha: inércia térmica, rampas, diâmetro ligado a rotação/velocidade, falhas aleatórias |
| `simulador.py` | Servidor Modbus TCP (`SimDevice` + `server.async_setValues`, API do pymodbus 3.16) |
| `leitor.py` | Cliente que lê tudo numa requisição só, carimba o timestamp em UTC e decodifica pelo dicionário |

O **dicionário é a fonte única da verdade**: o simulador e o leitor usam o mesmo arquivo. Para mudar um endereço, uma escala ou a ordem de palavras, edite só o CSV.

## Mapa de registradores (hipótese do dicionário)

| HR | Tag | Tipo | Observação |
|---|---|---|---|
| 0–1 | EXT01_TEMP_ZONA1 | FLOAT32 ABCD | °C |
| 2–3 | EXT01_TEMP_ZONA3 | FLOAT32 ABCD | °C |
| 4–5 | EXT01_TEMP_COLAR | FLOAT32 ABCD | °C |
| 6–7 | EXT01_TEMP_CABECA | FLOAT32 ABCD | °C |
| 10 | EXT01_ROSCA_RPM | UINT16 × 0,1 | 1234 = 123,4 rpm |
| 11 | LIN01_VELOCIDADE | UINT16 | m/min |
| 12–13 | LIN01_DIAMETRO | FLOAT32 ABCD | mm |
| 20 | bit 0: EXT01_FALHA · bit 1: LIN01_FALHA_PUXADOR · bit 2: LIN01_EMERGENCIA | bits | 3 sinais no mesmo registrador |

Os registradores 8–9 e 14–19 ficam vagos e são lidos como 0. Num CLP real também há buracos assim, e ler o bloco inteiro ainda compensa.

## Atividades de 19/11: tipos de dados no Modbus

1. **Rode simulador e leitor** e compare o log do simulador com a tabela do leitor. Os valores precisam bater.
2. **Leia os testes de `test_codec.py`.** Eles mostram, com números, como 25,5 vira `[0x41CC, 0x0000]` em ABCD e `[0x0000, 0x41CC]` em CDAB.
3. **Ordem errada:** rode `uv run leitor --uma-vez --ordem-forcada CDAB`. As temperaturas viram números absurdos, mas repare no `EXT01_TEMP_CABECA`: ele pode aparecer como `0.0` e **passar na checagem de faixa**. A ordem errada nem sempre se denuncia. Por isso, na integração real, sempre confira alguns valores com a IHM do CLP.
4. **Mude a ordem no dicionário:** troque `big-endian (ABCD)` por `CDAB` numa temperatura do CSV e rode os dois de novo. Como os dois usam o mesmo dicionário, continua batendo.
5. **Inteiro escalado:** mude a escala do RPM de `0,1` para `1` no CSV e veja o que perde (casas decimais) e o que ganha (faixa até 65535).
6. **Bits:** rode `uv run simulador --sem-falhas` e provoque uma falha pelo código (`extrusora.forcar_falha(...)` em `executar`). Veja o registrador 20 mudar: `0b001`, `0b100` etc.
7. **Compare com o pymodbus:** no leitor, decodifique uma temperatura também com `client.convert_from_registers(regs, data_type=client.DATATYPE.FLOAT32)` e confira que dá o mesmo valor do `codec.py`.

Próximo passo (20/11, opcional): escala 4–20 mA → unidade de engenharia, mais realismo no simulador.

## Observação sobre o pymodbus

O código do servidor segue a API documentada do pymodbus 3.16. Ele não pôde ser executado com o pymodbus real no ambiente onde foi escrito; o fluxo simulador → leitor foi testado com um pymodbus falso em memória. Se algo falhar no primeiro `uv run simulador`, o ponto a olhar é `criar_dispositivo()` e a chamada `server.async_setValues` em `simulador.py`.
