# mini-SMARTEX

Versão de estudo, em escala reduzida, do pipeline de dados do projeto SMARTEX (extrusão de cabos, MPT Cable). O plano completo está em `../../../docs/plano-de-estudos.md`, seção 8.

Etapas feitas: **simulador da extrusora v1** (19/11) e **sinal analógico 4–20 mA** (20/11).

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
uv run simulador --fio-rompido 30 # transmissor de pressão com fio rompido nos primeiros 30 s
uv run simulador --curto 30       # transmissor de pressão em curto (22 mA) nos primeiros 30 s
```

Terminal 2, o leitor:

```bash
uv run leitor                     # lê e decodifica a cada 1 s
uv run leitor --uma-vez           # lê uma vez e sai
uv run leitor --ordem-forcada CDAB  # exercício: lê os FLOAT32 na ordem errada
uv run leitor --reconexao 2       # tenta reconectar a cada 2 s (padrão: 5 s)
```

Se o simulador cair (ou ainda não estiver rodando), o leitor avisa e tenta reconectar sozinho, sem precisar ser reiniciado. Com `--uma-vez` ele tenta conectar uma única vez e sai. O intervalo padrão fica na constante `INTERVALO_RECONEXAO_S`, no topo de `leitor.py`.

Os timestamps dos logs e do leitor usam o fuso definido em `tempo.py` (hoje `America/Sao_Paulo`). Para trocar, altere só a constante `FUSO` lá.

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
| `codec.py` | Converte valores ↔ registradores de 16 bits: float32 (ABCD/CDAB), inteiro escalado, bits e sinal 4–20 mA (escala, saturação e diagnóstico NAMUR NE43). Feito à mão com `struct`, para entender o que acontece por baixo |
| `mapa.py` | Monta todos os registradores a partir das tags (simulador), decodifica de volta (leitor) e diagnostica os sinais 4–20 mA |
| `processo.py` | Modelo simples da linha: inércia térmica, rampas, diâmetro ligado a rotação/velocidade, pressão de massa, falhas aleatórias de processo e de sensor (fio rompido, curto) |
| `simulador.py` | Servidor Modbus TCP (`SimDevice` + `server.async_setValues`, API do pymodbus 3.16) |
| `leitor.py` | Cliente que lê tudo numa requisição só, reconecta sozinho se o servidor cair, carimba o timestamp e decodifica pelo dicionário |
| `tempo.py` | Fuso horário único do projeto (`FUSO`) e logging com timestamp nesse fuso, usados pelo simulador e pelo leitor |

O **dicionário é a fonte única da verdade**: o simulador e o leitor usam o mesmo arquivo. Para mudar um endereço, uma escala ou a ordem de palavras, edite só o CSV. Esse CSV fica em `docs/dicionario-de-dados.csv`, **fora desta pasta** (no diretório do projeto SMARTEX), e precisa conter a tag `EXT01_PRESSAO_MASSA` e as colunas `faixa_instrumento_min/max`.

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
| 14 | EXT01_PRESSAO_MASSA | 4–20 mA em µA | 12000 = 12 mA = 250 bar (faixa 0–500 bar) |
| 20 | bit 0: EXT01_FALHA · bit 1: LIN01_FALHA_PUXADOR · bit 2: LIN01_EMERGENCIA | bits | 3 sinais no mesmo registrador |

Os registradores 8–9 e 15–19 ficam vagos e são lidos como 0. Num CLP real também há buracos assim, e ler o bloco inteiro ainda compensa.

## Atividades de 19/11: tipos de dados no Modbus

1. **Rode simulador e leitor** e compare o log do simulador com a tabela do leitor. Os valores precisam bater.
2. **Leia os testes de `test_codec.py`.** Eles mostram, com números, como 25,5 vira `[0x41CC, 0x0000]` em ABCD e `[0x0000, 0x41CC]` em CDAB.
3. **Ordem errada:** rode `uv run leitor --uma-vez --ordem-forcada CDAB`. As temperaturas viram números absurdos, mas repare no `EXT01_TEMP_CABECA`: ele pode aparecer como `0.0` e **passar na checagem de faixa**. A ordem errada nem sempre se denuncia. Por isso, na integração real, sempre confira alguns valores com a IHM do CLP.
4. **Mude a ordem no dicionário:** troque `big-endian (ABCD)` por `CDAB` numa temperatura do CSV e rode os dois de novo. Como os dois usam o mesmo dicionário, continua batendo.
5. **Inteiro escalado:** mude a escala do RPM de `0,1` para `1` no CSV e veja o que perde (casas decimais) e o que ganha (faixa até 65535).
6. **Bits:** rode `uv run simulador --sem-falhas` e provoque uma falha pelo código (`extrusora.forcar_falha(...)` em `executar`). Veja o registrador 20 mudar: `0b001`, `0b100` etc.
7. **Compare com o pymodbus:** no leitor, decodifique uma temperatura também com `client.convert_from_registers(regs, data_type=client.DATATYPE.FLOAT32)` e confira que dá o mesmo valor do `codec.py`.

## Atividades de 20/11: sinal analógico 4–20 mA

O projeto vai instalar "cartões de entrada analógica". Um transmissor converte a grandeza física (pressão, temperatura) numa corrente de 4 a 20 mA, e o cartão do CLP mede essa corrente. Aqui o cartão entrega a corrente em **µA** num registrador (4000 a 20000).

Para o exemplo entrou uma tag nova no dicionário, `EXT01_PRESSAO_MASSA` (pressão de massa no cabeçote, faixa 0–500 bar). Ela está marcada como **sugestão**, porque não aparece no documento do projeto.

**Conceitos**

- **Conversão em duas etapas:** registrador → mA (`µA ÷ 1000`) → unidade de engenharia (`mín + (mA − 4) ÷ 16 × (máx − mín)`). A faixa do transmissor (`faixa_instrumento_min/max`) é uma coluna nova do dicionário, diferente da faixa válida.
- **Por que 4 mA e não 0:** é o "zero vivo". 0 mA passa a significar "sem sinal" (fio rompido, transmissor desligado), e isso permite diagnosticar o instrumento.
- **NAMUR NE43**, o padrão de fato:
  - 3,8 a 20,5 mA: faixa de medição. Abaixo de 4 ou acima de 20, o valor está saturado.
  - Abaixo de 3,6 mA ou acima de 21 mA: falha do sensor.
  - Entre essas faixas: zona indefinida.
- **Falha de sensor ≠ falha de processo.** Fio rompido (0 mA) ou curto (22 mA) não param a linha, só estragam a medição. Por isso o leitor mostra `—` em vez de um número: converter 0 mA daria −125 bar, lixo com cara de valor. Em dado de produção, isso vira flag de qualidade, não alerta de processo.

**Exercícios**

1. Rode `uv run leitor` e confira a coluna de qualidade da pressão: a corrente em mA aparece ao lado do valor em bar. Faça a conta de cabeça: 12 mA ≈ 250 bar.
2. Rode `uv run simulador --fio-rompido 30` e veja o leitor mostrar `FALHA: CORRENTE BAIXA` por 30 s e depois voltar sozinho. Repita com `--curto 30` e compare: agora a corrente é 22 mA e o diagnóstico é `FALHA: CORRENTE ALTA`. As duas opções não podem ser usadas juntas.
3. Repare que a pressão cai quando a extrusora para (a rosca para de girar) e sobe um pouco quando o cabeçote esfria (material mais viscoso). Provoque uma falha da extrusora e observe.
4. **Faixa que não começa em zero:** mude `faixa_instrumento_min` da pressão para `50` no CSV e rode os dois de novo. O que muda na corrente para a mesma pressão?
5. **Outra convenção de cartão:** CLPs Siemens costumam entregar 4–20 mA como contagem de 0 a 27648, e não em µA. Escreva em `codec.py` uma função `contagem_siemens_para_ma` e um teste para ela. *Feito em 23/11:* `contagem_siemens_para_ma`, o inverso `ma_para_contagem_siemens` (satura em −4864..32511, o under/overrange do cartão) e `registrador_para_int16` (a contagem tem sinal, mas o Modbus entrega uint16). Testes no fim de `tests/test_analogico.py`.
6. Leia `tests/test_analogico.py`: ele cobre escala, saturação, NE43 e os casos de fio rompido e curto.

## Observação sobre o pymodbus

O código do servidor segue a API documentada do pymodbus 3.16. Ele não pôde ser executado com o pymodbus real no ambiente onde foi escrito; o fluxo simulador → leitor foi testado com um pymodbus falso em memória. Se algo falhar no primeiro `uv run simulador`, o ponto a olhar é `criar_dispositivo()` e a chamada `server.async_setValues` em `simulador.py`.
