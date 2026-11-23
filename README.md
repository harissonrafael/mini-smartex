# mini-SMARTEX

Versão de estudo, em escala reduzida, do pipeline de dados do projeto SMARTEX (extrusão de cabos, MPT Cable). O plano completo está em `../../../docs/plano-de-estudos.md`, seção 8.

Etapas feitas: **simulador da extrusora v1** (19/11), **sinal analógico 4–20 mA** (20/11) e **OPC-UA com StatusCode e timestamps** (23/11).

## Como rodar

Requisitos: Python 3.13+ e [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

Terminal 1, o "CLP" simulado:

```bash
uv run simulador                  # Modbus TCP em 127.0.0.1:5020 + OPC-UA em 127.0.0.1:4840
uv run simulador --semente 42     # mesma sequência de valores toda vez
uv run simulador --sem-falhas     # desliga falhas aleatórias
uv run simulador --fio-rompido 30 # transmissor de pressão com fio rompido nos primeiros 30 s
uv run simulador --curto 30       # transmissor de pressão em curto (22 mA) nos primeiros 30 s
uv run simulador --sem-opcua      # só Modbus
uv run simulador --desvio-relogio 90  # relógio do "CLP" 90 s adiantado (sem NTP)
```

Terminal 2, o leitor (Modbus ou OPC-UA, ou os dois em terminais separados):

```bash
uv run leitor                     # lê e decodifica a cada 1 s
uv run leitor --uma-vez           # lê uma vez e sai
uv run leitor --ordem-forcada CDAB  # exercício: lê os FLOAT32 na ordem errada
uv run leitor --reconexao 2       # tenta reconectar a cada 2 s (padrão: 5 s)

uv run leitor-opcua               # OPC-UA por polling: valor, StatusCode, SourceTimestamp e ServerTimestamp
uv run leitor-opcua --uma-vez     # lê uma vez e sai
uv run leitor-opcua --assinatura  # subscription: o servidor avisa só quando algo muda
```

Se o simulador cair (ou ainda não estiver rodando), os dois leitores avisam e tenta reconectar sozinho, sem precisar ser reiniciado. Com `--uma-vez` ele tenta conectar uma única vez e sai. O intervalo padrão fica na constante `INTERVALO_RECONEXAO_S`, no topo de `leitor.py`.

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
                          escalado, bits)           │
                                │                   │ mesma memória
                                ▼                   ▼
                    opcua_comum.py  ──►  servidor_opcua.py  ──OPC-UA──►  leitor_opcua.py
                    (valor de engenharia     (asyncua: DataValue com
                     + StatusCode)            StatusCode e 2 timestamps)
```

| Arquivo | Papel |
|---|---|
| `dicionario.py` | Lê o CSV do dicionário. Valida endereços Modbus e OPC-UA (`endereco_opcua`) e a sobreposição de registradores e de nós |
| `codec.py` | Converte valores ↔ registradores de 16 bits: float32 (ABCD/CDAB), inteiro escalado, bits e sinal 4–20 mA (escala, saturação e diagnóstico NAMUR NE43). Feito à mão com `struct`, para entender o que acontece por baixo |
| `mapa.py` | Monta todos os registradores a partir das tags (simulador), decodifica de volta (leitor) e diagnostica os sinais 4–20 mA |
| `processo.py` | Modelo simples da linha: inércia térmica, rampas, diâmetro ligado a rotação/velocidade, pressão de massa, falhas aleatórias de processo e de sensor (fio rompido, curto) |
| `simulador.py` | Servidor Modbus TCP (`SimDevice` + `server.async_setValues`, API do pymodbus 3.16) e, junto, o servidor OPC-UA |
| `leitor.py` | Cliente que lê tudo numa requisição só, reconecta sozinho se o servidor cair, carimba o timestamp e decodifica pelo dicionário |
| `opcua_comum.py` | Parte do OPC-UA sem rede: NodeIds, severidade do StatusCode (Good/Uncertain/Bad), diagnóstico NE43 → StatusCode, relógio do CLP com desvio e atraso entre timestamps |
| `servidor_opcua.py` | Servidor OPC-UA (asyncua): uma variável por tag, com unidade (`EngineeringUnits`) e faixa (`EURange`), publicada a partir da mesma memória do Modbus |
| `leitor_opcua.py` | Cliente OPC-UA: polling com uma requisição Read para todos os nós, ou subscription; mostra StatusCode e os dois timestamps; reconecta sozinho |
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

## Atividades de 23/11: OPC-UA em produção

O simulador agora faz o que muitos CLPs modernos fazem: expõe a mesma memória por Modbus e por OPC-UA. O servidor OPC-UA lê os registradores montados para o Modbus e os decodifica pelo dicionário, então as duas interfaces mostram sempre o mesmo valor. Espaço de endereços: `Objects/LinhaExtrusao01/<tag>`, com NodeId `ns=<i>;s=<tag>` no namespace `urn:mini-smartex:extrusora`.

**O que muda em relação ao Modbus**

| | Modbus | OPC-UA |
|---|---|---|
| O que chega | Registradores de 16 bits crus | `DataValue`: valor já em unidade de engenharia, com tipo (Double, Boolean) |
| Quem sabe endereço, escala e ordem de palavras | O cliente, pelo dicionário | O servidor. O cliente só precisa do NodeId |
| Qualidade | Nenhuma. O leitor deduz pela corrente (NE43) | `StatusCode`, dito pela própria fonte |
| Hora | Nenhuma. O coletor carimba ao ler | `SourceTimestamp` (fonte) e `ServerTimestamp` (servidor) |
| Mudanças | Só por polling | Polling ou subscription (o servidor avisa) |

**StatusCode.** É um inteiro de 32 bits. Os 2 bits mais altos dão a severidade: `00` Good, `01` Uncertain, `10` Bad. O resto é o motivo. O diagnóstico NE43 da pressão vira StatusCode assim:

| Corrente | Diagnóstico NE43 | StatusCode |
|---|---|---|
| 4–20 mA | ok | `Good` |
| 3,8–4 ou 20–20,5 mA | saturado | `UncertainEngineeringUnitsExceeded` |
| 3,6–3,8 ou 20,5–21 mA | indefinido | `UncertainSensorNotAccurate` |
| < 3,6 ou > 21 mA | falha (fio rompido ou curto) | `BadSensorFailure`, com **valor nulo** |
| antes do primeiro passo | — | `BadWaitingForInitialData` |

Com StatusCode Bad, a especificação (Parte 4, §7.7.1) manda o servidor entregar o valor como nulo. O asyncua aplica isso sozinho, e por isso o servidor nem tenta repetir o último valor. Regra para o coletor: **Good** entra normal, **Uncertain** entra com flag de qualidade e **Bad** registra a ocorrência, mas não entra em cálculo.

**SourceTimestamp × ServerTimestamp.** O primeiro é a hora em que a fonte amostrou o valor, no relógio da fonte. O segundo é a hora em que o servidor OPC-UA recebeu ou publicou o valor, no relógio do servidor. Num CLP com servidor embutido, os dois relógios são o mesmo. Num gateway separado, não são. O leitor mostra a diferença na coluna `Δ ms`. Os timestamps trafegam em UTC, e o leitor os exibe no fuso de `tempo.FUSO`.

**Exercícios**

1. Rode `uv run simulador` e, em dois terminais, `uv run leitor` e `uv run leitor-opcua`. Confira que os valores batem e compare o que cada um precisa saber para chegar a eles.
2. Rode `uv run simulador --curto 30` com o `leitor-opcua`. A pressão aparece como `BadSensorFailure`, com valor `—`, e volta a `Good` sozinha. Compare com a coluna de qualidade do leitor Modbus.
3. **Relógio sem NTP:** rode `uv run simulador --desvio-relogio 90`. O `Δ ms` fica em −90000: a fonte está 90 s adiantada. Se o coletor gravasse o SourceTimestamp, as leituras iriam para o futuro, e cruzá-las com outra fonte daria errado. É a hipótese "Relógios" do plano (§7): sem NTP garantido, o coletor carimba a própria hora e guarda o SourceTimestamp numa coluna à parte, para auditoria.
4. Rode `uv run leitor-opcua --assinatura`. Os bits de falha não aparecem a cada segundo: o servidor só notifica quando o valor ou o StatusCode mudam (`DataChangeTrigger StatusValue`). Em 11 tags, quantas notificações chegam por segundo? Compare com o polling, que lê as 11 sempre.
5. Derrube o simulador com o `leitor-opcua --assinatura` rodando. O leitor recebe `BadShutdown` na assinatura, avisa a queda e recria a assinatura quando o simulador volta.
6. **Para pensar:** o dicionário só tinha endereços Modbus (`HR 0–1`). Para o coletor ler OPC-UA de um CLP real, ele precisa de uma coluna de NodeId. Ele seria `ns=<i>;s=...`? E o índice do namespace pode ser fixo? *Resolvido em 23/11,* veja abaixo.

**Resposta do exercício 6: a coluna `endereco_opcua`**

O índice **não pode** ser fixo. Em `ns=2;s=...`, o `2` é só a posição da URI na tabela de namespaces do servidor, e essa posição muda quando o CLP é atualizado, quando entra um namespace novo antes do seu ou quando a leitura passa por um gateway. A URI não muda. Por isso o dicionário guarda o NodeId na forma `nsu=` da especificação:

```
nsu=urn:mini-smartex:extrusora;s=EXT01_TEMP_ZONA1
```

- **`dicionario.py`** lê a coluna e recusa `ns=<número>`, explicando por quê. Também recusa formato inválido e o mesmo nó usado por duas tags.
- **`leitor_opcua.py`** lê a tabela de namespaces a cada conexão (`get_namespace_array`) e troca a URI pelo índice (`resolver_node_id`). Se o dicionário citar uma URI que o servidor não tem, ele para com um erro de configuração em vez de ficar reconectando: reconectar não resolve.
- **`servidor_opcua.py`** cria cada nó exatamente no endereço do dicionário e registra toda URI citada lá. Sem a coluna, vale `nsu=urn:mini-smartex:extrusora;s=<tag>`.

O identificador só é o nome da tag no simulador. Num CLP real, quem define é o fabricante: `nsu=<URI do S7>;s="DB_Extrusora"."TempZona1"` no Siemens S7-1500 (as aspas fazem parte do identificador), `s=MAIN.fTempZona1` no Beckhoff, `i=1042` em servidores com identificador numérico. A `tag` continua sendo a chave estável do pipeline; o NodeId é só o endereço na origem, como o `HR 0–1`.

Para ver na prática: copie o dicionário, troque o endereço da pressão por `nsu=http://exemplo/simatic-s7-opcua;s="DB_Extrusora"."PressaoMassa"` e rode `uv run simulador --dicionario <cópia>` e `uv run leitor-opcua --dicionario <cópia>`. O servidor cria o namespace 3 para essa URI e o leitor acha o nó por ela. Depois rode só o leitor com a cópia, contra o simulador com o dicionário original, e veja o erro de namespace desconhecido.

Os testes ficam em `tests/test_opcua.py`. O último sobe um servidor OPC-UA de verdade numa porta livre e lê dele com um cliente de verdade.

**Segurança.** O servidor roda sem criptografia nem login (`NoSecurity`), só para estudo, e o asyncua avisa disso no log. Na rede OT de verdade, use `Basic256Sha256` com certificados.

## Observação sobre o pymodbus

O código do servidor segue a API documentada do pymodbus 3.16. Em 23/11, o simulador rodou com o pymodbus real e o leitor Modbus leu dele normalmente. Se algo falhar depois de uma atualização do pymodbus, o ponto a olhar é `criar_dispositivo()` e a chamada `server.async_setValues` em `simulador.py`.
