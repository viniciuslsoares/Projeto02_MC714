# Integração: serviço de tarefas com coordenador eleito

Implementação (issue #8):
- `src/tarefas.py`: o despachante, que roda em todos os nós;
- `src/cliente.py`: o cliente e gerador de carga;
- `experiments/integracao.py` e `scenarios/integracao/*.yaml`: as execuções e as métricas;
- as propriedades da aplicação, conferidas em `analysis/log_verifier.py`.

Testes: `tests/test_integracao.py`.

## 1. Problema e requisitos

- **Problema:** executar tarefas em lote, como num escalonador de jobs.
  - Um cliente manda tarefas, e um coordenador as distribui a workers.
  - Quando um worker ou o próprio coordenador cai, nenhuma tarefa pode se perder, e o serviço continua.
- **Como os dois algoritmos entram.** O serviço só usa as APIs deles, sem algoritmo novo:
  - **Detector de falhas (#3):** o coordenador só atribui tarefas aos workers de quem não suspeita (`detector.vivos()`).
  - **Bully (#4):** elege o coordenador (`bully.lider`). Quando ele cai, os outros elegem o maior vivo, e o serviço continua com o novo líder.
- **Requisitos:**
  - **R1 (vivacidade):** toda tarefa enviada pelo cliente é confirmada, e só depois de executada por um worker.
  - **R2 (segurança):** nenhuma tarefa fica com dois líderes ao mesmo tempo.
  - **R3 (disponibilidade):**
    - a queda de um worker não interrompe o serviço;
    - a queda do líder interrompe o serviço só até o novo líder ser eleito (detecção mais eleição, `docs/modelo_bully.md` §3).
  - **Execução "pelo menos uma vez":** uma tarefa pode rodar mais de uma vez (§3), então as tarefas precisam ser idempotentes.

## 2. Arquitetura e protocolo

- Todos os nós rodam o mesmo processo (`python -m src.node`): detector, Bully e despachante.
  - O líder é o coordenador; os outros nós são os workers.
  - O líder não executa tarefas.
- Mensagens (`POST /msg`, como as dos algoritmos):

| Mensagem | De → para | Conteúdo | Resposta |
|---|---|---|---|
| `TASK` | cliente → nó que ele acha que é o líder | `id`, `ms` (duração), `pedido` (o número do pedido do cliente para essa tarefa) | `feita` (com o worker), `falhou` ou `nao_sou_lider` (com o líder que o nó conhece) |
| `EXEC` | líder → worker | a mesma tarefa | `feita: true` depois de executar, ou `feita: false` se quem pediu não é o líder do worker |

- **Uma tarefa sem falhas:**
  1. O cliente manda `TASK` ao nó que acha que é o líder. Se ele não for, responde `nao_sou_lider` com o líder que conhece, e o cliente pede a esse.
  2. O líder escolhe um worker entre `detector.vivos()`, em rodízio, e manda `EXEC`.
  3. O worker confere que quem pediu é o seu líder, executa (o trabalho é simulado com uma espera de `ms`) e responde.
  4. O líder responde `feita` ao cliente.
- **Queda de um worker:**
  - Um `EXEC` sem resposta (conexão recusada ou timeout), ou recusado, faz o líder reatribuir a tarefa a outro worker vivo, cada worker no máximo uma vez por pedido.
  - Se não sobra nenhum worker, o líder responde `falhou`.
  - Até o detector suspeitar do worker caído (cerca de `HB_TIMEOUT`), o líder ainda pode escolhê-lo. Com processos locais, cada uma dessas tentativas falha na hora, e a tarefa é reatribuída (a coluna `reatribuidas` das métricas).
- **Queda do líder:**
  - O pedido do cliente fica sem resposta, e o cliente tenta o próximo nó.
  - Até a detecção e a eleição, os outros nós ainda apontam o líder caído; depois, apontam o novo líder, que aceita a tarefa.
  - O cliente repete o pedido a cada 0,1 s: sem resposta, com `falhou` ou com um nó sem líder.
- **Volta de um nó com ID maior que o líder:**
  - Ele vence a eleição e anuncia a todos.
  - O líder anterior para de aceitar `TASK` assim que recebe o `COORDINATOR`.
  - Os workers que já receberam o `COORDINATOR` recusam `EXEC` do líder anterior.
- **Timeouts:**
  - `EXEC`: `ms + RPC_TIMEOUT`. O líder segura um pedido por no máximo `(n − 1)(ms + RPC_TIMEOUT)`, uma tentativa por worker.
  - `TASK`: `n (ms + RPC_TIMEOUT)`, maior que isso. Assim, o cliente só faz outro pedido da mesma tarefa depois que o anterior terminou (§4).
  - O cliente desiste de uma tarefa 30 s depois do primeiro envio.

## 3. Modelo do sistema combinado

- **Temporal: parcialmente síncrono**, como o detector e o Bully (`docs/modelo_detector.md` §2, `docs/modelo_bully.md` §2).
  - Os timeouts do §2 e as propriedades do §4 valem nos períodos síncronos.
- **Canais:** os de `docs/modelo_falhas.md` §6: sem FIFO, com perda e sem retransmissão, mas sem atraso nem duplicação injetados (`NET_DELAY_MS`, `NET_JITTER_MS` e `NET_DUP_PROB` em 0).
  - Com atraso ou duplicação, um `EXEC` passaria do timeout do §2, porque `send()` espera o atraso antes do POST e, na duplicação, faz o POST duas vezes (`docs/modelo_falhas.md` §7). Aí o cliente poderia reenviar com o pedido anterior ainda no líder.
  - O serviço não depende de ordem: cada tarefa tem um `id`, e cada pedido, um número.
  - Quem retransmite é o cliente, fim a fim: ele repete o pedido até a confirmação.
- **Falhas:**
  - crash-stop e crash-recovery dos workers e do líder;
  - um nó que volta recomeça sem estado (o Bully não tem persistência) e recebe tarefas de novo assim que conhece o líder;
  - o cliente não falha. Se ele cair, as tarefas que ainda não foram confirmadas se perdem: quem guarda cada tarefa até a confirmação é ele.
  - Partições não fazem parte dos cenários daqui (§8).
- **Relógios:** só o relógio monotônico local, nos timeouts. O relógio de Lamport só ordena os logs.
- **Persistência:** nenhuma.
  - O líder e os workers não guardam tarefas, então um novo líder não precisa recuperar estado: o cliente reenvia o que ainda não foi confirmado.
  - O preço é a semântica **"pelo menos uma vez"** (van Steen e Tanenbaum, cap. 8, semântica de RPC na presença de falhas). Se o líder cai depois de mandar o `EXEC`, o cliente não sabe se a tarefa rodou e a reenvia. Ela pode rodar duas vezes (coluna `executadas_2x`).
  - "Exatamente uma vez" pediria deduplicação com estado persistido ou replicado, o que fica fora do escopo.
- **O worker só executa para o próprio líder.** Isso impede que um líder antigo, que ainda não soube da troca, use um worker que já soube. Não é uma época (`docs/modelo_bully.md` §3): um worker que também não soube da troca ainda aceita o líder antigo.

## 4. Propriedades e verificação

O verificador de logs confere as duas propriedades quando há tarefas nos logs. Ele também lê o `cliente.jsonl` da pasta. Elas aparecem junto com as 6 propriedades do detector e do Bully (`docs/testes.md` §3), conferidas sobre os mesmos logs.

| Propriedade | Tipo | O que confere |
|---|---|---|
| `tarefas.execucao` | vivacidade (R1) | Toda tarefa enviada pelo cliente (`tarefa_enviada`) tem uma `tarefa_confirmada`, e antes dela houve um `task_end` dessa tarefa em algum worker. O `[OK]` diz quantas tarefas foram executadas mais de uma vez. |
| `tarefas.um_lider` | segurança (R2) | Para cada tarefa, os intervalos em que ela esteve com cada líder não se sobrepõem entre líderes diferentes. O intervalo vai do primeiro `task_assign` de um pedido ao `task_reply` dele; se o líder caiu antes de responder, vai até o fim da vida do líder. |

- **Por que R2 vale:**
  - O cliente tem no máximo um pedido em aberto por tarefa. Ele só faz outro depois que o anterior terminou, com uma resposta ou com o timeout, que é maior que o tempo máximo de um pedido no líder (§2).
  - Um líder que cai encerra o pedido que tinha.
  - Logo, no período síncrono, dois líderes nunca têm a mesma tarefa ao mesmo tempo, nem durante a troca de líder, em que os dois se acham líderes por um instante.
- **Por que R1 vale:**
  - O líder só responde `feita` depois do `feita` do worker, e o worker só responde depois de executar.
  - O cliente repete o pedido até a confirmação.
  - Depois de uma falha, o Bully elege um líder (vivacidade do Bully), e o detector deixa de suspeitar dos workers vivos (precisão eventual). Enquanto houver um worker vivo, um pedido acaba sendo atendido.
- **Testes:**
  - `test_despachante` (unitário):
    - um nó que não é o líder responde `nao_sou_lider`;
    - o worker recusa `EXEC` de quem não é o seu líder;
    - o líder sem worker vivo responde `falhou`;
    - com dois workers caídos, o líder tenta cada um uma vez antes de desistir.
  - `test_verificador_tarefas` (logs sintéticos):
    - uma execução correta dá `[OK]`;
    - o líder cai com uma tarefa em execução, e ela roda de novo no novo líder: também dá `[OK]`, com uma tarefa executada duas vezes;
    - uma tarefa com dois líderes ao mesmo tempo, uma confirmada sem execução e uma nunca confirmada: o verificador acusa as duas propriedades.
  - `test_integracao_com_processos_reais`: o cenário de falha em escala menor, com o executor da §5, um processo por nó e carga. Todas as tarefas são confirmadas, há reatribuições e o verificador não acusa as propriedades da aplicação.

## 5. Como rodar

```bash
python -m experiments.integracao                     # os 2 cenários, 5 repetições (~6 min)
python -m experiments.integracao scenarios/integracao/quedas.yaml --repeticoes 1 --saida /tmp/teste
python -m experiments.run_all --grades integracao    # o mesmo que o primeiro, pela porta de entrada única
COMPOSE_PROFILES=integracao ./scripts/run_scenario.sh scenarios/integracao/quedas.yaml   # no compose
```

- **Com processos locais:**
  - Sem `--saida`, os CSVs versionados são sobrescritos: use outra pasta para rodadas parciais.
  - Ctrl+C ou SIGTERM param os nós e gravam as execuções que já terminaram.
  - O executor sobe 3 nós como nos cenários da #7 (`docs/cenarios.md` §2) e roda o cliente numa thread durante o cenário.
  - Os logs ficam em `results/raw/integracao/<cenário>_seed<semente>/`, inclusive o `cliente.jsonl`, e podem ser conferidos com `python -m analysis.log_verifier <pasta>`.
- **No compose** (a demonstração ponta a ponta, candidata ao vídeo):
  - Com `COMPOSE_PROFILES=integracao`, o `docker compose up` do `run_scenario.sh` também sobe o serviço `cliente`.
  - O cliente começa quando os 3 nós estão saudáveis e gera carga por `DURACAO_S` (30 s). O injetor aplica os eventos do cenário.
  - No fim, o script copia os logs, inclusive o `cliente.jsonl`, e roda o verificador, que confere também as propriedades da aplicação.
  - Em outro terminal, `docker compose logs -f cliente` mostra a cada segundo quantas tarefas foram confirmadas e a que nó o cliente está pedindo: o líder, ou, durante a queda dele, os nós que o cliente tenta até achar o novo.
  - O `DURACAO_S` do cliente não pode passar do `duration_s` do cenário. Se passar, o script copia os logs com o cliente ainda rodando, e o `tarefas.execucao` acusa tarefas não confirmadas.
  - O cliente não publica porta nenhuma. Esse caminho não foi testado com Docker nesta máquina.

## 6. Execução e métricas

- **Configuração:**
  - 3 nós, os do compose: o líder 3 e os workers 1 e 2;
  - carga aberta de 20 tarefas/s, com cada tarefa saindo no seu instante, responda o serviço ou não;
  - tarefas de 100 ms e 30 s de carga;
  - os padrões do compose: `HB_INTERVAL` 0,5 s, `HB_TIMEOUT` 2,0 s, `RPC_TIMEOUT` 1,0 s;
  - 2 s de aquecimento depois de todos terem o líder 3, e 5 repetições por cenário.
- **Sementes:** a repetição r usa `SEED` = `seed` do YAML + r − 1. Sem perda nem atraso sorteados, a semente não muda nada aqui: a variação vem do escalonamento e da fase das quedas em relação aos heartbeats.

| Cenário | Eventos (s) | O que mostra |
|---|---|---|
| `integracao_normal` | nenhum | A latência do serviço sem falhas: a tarefa mais duas idas e voltas HTTP. |
| `integracao_quedas` | 5: crash do worker 1; 10: volta do 1; 15: crash do líder 3; 22: volta do 3 | A reatribuição durante a detecção da queda do worker; a interrupção e o reenvio na queda do líder; o 3 retomando a liderança quando volta. |

- **Saída:**
  - `results/experimentos/integracao.csv`: uma linha por execução;
  - `results/experimentos/integracao_tarefas.csv`: uma linha por tarefa, para os percentis sobre todas as execuções e as figuras da issue #9;
  - `results/experimentos/integracao.csv.meta.json`: proveniência, parâmetros, cenários, ambiente e as colunas dos dois CSVs.

| Coluna | Definição |
|---|---|
| `tarefas`, `confirmadas` | Tarefas enviadas pelo cliente, e quantas delas foram confirmadas como feitas. |
| `vazao_s` | Tarefas confirmadas por segundo, do primeiro envio à última confirmação. |
| `lat_p50_ms`, `lat_p95_ms`, `lat_p99_ms`, `lat_max_ms` | Latência de cada tarefa, do primeiro envio à confirmação (inclui os reenvios), em percentis e no máximo. |
| `maior_pausa_s` | O maior intervalo entre duas confirmações seguidas: a interrupção do serviço. |
| `reatribuidas` | Tarefas com mais de um `task_assign`: reatribuídas pelo líder a outro worker, ou reenviadas pelo cliente a outro líder. |
| `executadas_2x` | Tarefas executadas mais de uma vez (mais de um `task_end`): o custo do "pelo menos uma vez". |
| `avisos_verificador` | Propriedades com `[AVISO]` no verificador, inclusive as da aplicação. |

- **Por que essas métricas:**
  - a latência em percentis é o que o cliente percebe;
  - a maior pausa é o tempo de reparo do serviço depois da queda do líder (o MTTR da Aula 3);
  - `reatribuidas` e `executadas_2x` são o custo de mascarar as falhas.
- **O que se espera:**
  - **Sem falhas:** latência de cerca de 100 ms mais algumas idas e voltas HTTP locais, e vazão igual à carga oferecida.
  - **Queda do worker:**
    - Até a suspeita, o líder ainda escolhe o worker caído para metade das tarefas. São cerca de 20 tarefas/s × 2 s / 2 = 20 reatribuições.
    - Cada reatribuição custa pouco, porque a conexão é recusada na hora.
  - **Queda do líder:**
    - Nenhuma confirmação até a detecção (de 1,5 a 2,5 s depois da queda, conforme a fase dos heartbeats) e a eleição.
    - As tarefas enviadas nesse período esperam, e isso aparece no p95 e no p99.
    - As tarefas que estavam em execução, cerca de 20 tarefas/s × 0,1 s = 2, rodam de novo.

## 7. Resultados

Dados:
- `results/experimentos/integracao.csv` e `integracao_tarefas.csv`, com 5 execuções de cada cenário (600 tarefas por execução);
- geradas pelo `run_all` sobre o commit registrado no `.meta.json`, sem alterações locais;
- o ambiente também está no `.meta.json`: Apple M2 Pro, macOS, CPython 3.14.

Intervalos de confiança e figuras ficam para a issue #9.

| Métrica: mediana das execuções (mín–máx) | `integracao_normal` | `integracao_quedas` |
|---|---|---|
| Tarefas confirmadas | 600/600 nas 5 | 600/600 nas 5 |
| Vazão (tarefas/s) | 19,96 (19,95–19,96) | 19,96 (19,95–19,96) |
| Latência p50 (ms) | 109,8 (109,4–111,3) | 110,6 (110,2–113,2) |
| Latência p95 (ms) | 114,7 (113,4–117,2) | 327,9 (321,0–555,4) |
| Latência p99 (ms) | 119,0 (115,2–121,2) | 1527,7 (1513,5–1682,3) |
| Latência máxima (ms) | 126,2 (120,7–130,9) | 1850,0 (1829,4–2020,3) |
| Maior pausa sem confirmações (s) | 0,07 (0,06–0,07) | 1,72 (1,70–1,89) |
| Tarefas reatribuídas | 0 | 20 (18–23) |
| Tarefas executadas duas vezes | 0 | 2 (2–2) |
| `[AVISO]` do verificador | nenhum | nenhum |

- Juntando as 3000 tarefas de cada cenário, os percentis p50/p95/p99 são:
  - sem falhas: 110,0/114,9/118,8 ms;
  - com quedas: 110,8/427,3/1618,5 ms.
- **R1 e R2:** valem em todas as execuções. Todas as tarefas foram confirmadas depois de executadas, nenhuma esteve com dois líderes ao mesmo tempo, e as 6 propriedades do detector e do Bully deram `[OK]`.
- **Sem falhas:** a latência é a tarefa (100 ms) mais cerca de 10 ms: duas idas e voltas HTTP locais, o log e o escalonamento das threads.
- **Queda do worker** (5 s):
  - Não muda a latência: o máximo das tarefas enviadas entre 5 e 8 s foi 125 ms.
  - Até o detector suspeitar do worker (cerca de 2 s), metade das tarefas vai para ele, a conexão é recusada na hora, e a tarefa vai para o outro worker. São as cerca de 20 reatribuídas.
  - Depois da suspeita, só o worker vivo recebe tarefas; quando o worker volta (10 s), recebe de novo.
- **Queda do líder** (15 s):
  - É dela toda a cauda: só as tarefas enviadas entre 13 e 18 s passaram de 200 ms (cerca de 34 por execução, de 600).
  - O serviço fica cerca de 1,7 s sem confirmar nada. É a detecção (de 1,5 a 2,5 s depois da queda, conforme a fase dos heartbeats) mais a eleição, que leva poucos ms com processos locais.
  - Por isso o p95 do cenário de falha fica em cerca de 330 ms, e o p99 e o máximo ficam perto da pausa.
  - Nas 5 execuções, as 2 tarefas que estavam no worker quando o líder caiu rodaram de novo no novo líder ("pelo menos uma vez").
  - Até achar o novo líder, uma tarefa pendente chegou a 32 pedidos (§8).
- **Volta do líder** (22 s):
  - O 3 vence a eleição e retoma a liderança sem custo para o cliente: o máximo das tarefas enviadas entre 22 e 25 s foi 125 ms.
  - Nenhum worker recusou `EXEC`, e nenhum pedido terminou em `falhou`.
- **Vazão:** é a carga oferecida nos dois cenários, porque as tarefas atrasadas pela queda do líder também são confirmadas (§8, trabalho simulado).

## 8. Limitações

- **Um cliente só, e ele não falha.** É ele que guarda as tarefas até a confirmação.
- **"Pelo menos uma vez".** Tarefas podem rodar duas vezes quando o líder cai; elas precisam ser idempotentes.
- **Trabalho simulado.**
  - A tarefa é uma espera, e cada worker executa quantas chegarem em paralelo, sem fila nem limite de capacidade.
  - Por isso a vazão acompanha a carga oferecida. O que as falhas mudam é a latência e a interrupção.
- **O cliente sonda durante a falha do líder.**
  - Cada tarefa pendente faz cerca de 20 pedidos por segundo até achar o novo líder (a coluna `pedidos` de `integracao_tarefas.csv`). A cada 0,1 s, o cliente pede a um nó que ainda aponta o líder caído e é redirecionado na hora para ele, sem resposta.
  - Um cliente real usaria espera exponencial.
- **Partições não testadas.**
  - Com o líder isolado sem workers, ele responde `falhou`, e o cliente procura no outro lado, que elege o seu líder (`docs/cenarios.md`).
  - O comportamento esperado é que o serviço continue do lado que tem workers, mas isso não foi medido.
- **O worker só aceita o próprio líder, sem época.** Na troca de líder, os dois líderes podem despachar ao mesmo tempo, para workers diferentes, por um instante. Mesmo assim, cada tarefa fica com um líder só, porque o cliente pede a um de cada vez (§4).
- **Com 3 nós, só há 2 workers.** Com o líder e um worker fora ao mesmo tempo, o serviço para até alguém voltar: o líder não executa tarefas.
- **Processos numa só máquina** (`docs/experimentos.md` §7). Com processos locais, um `EXEC` a um worker caído falha na hora (conexão recusada). No compose, a falha passa pelo Docker, que remove o nome do contêiner, e pode demorar mais. Isso não foi medido.
- **Carga com taxa fixa,** não um processo de Poisson. O cliente roda na mesma máquina que os nós: no executor local, no mesmo processo; no compose, num contêiner.

## Referências

- M. van Steen e A. S. Tanenbaum, *Distributed Systems*, 4ª ed.:
  - cap. 5: eleição de líder, o algoritmo do valentão e o coordenador;
  - cap. 8: tolerância a falhas, comunicação cliente-servidor confiável, semântica de RPC na presença de falhas ("pelo menos uma vez", idempotência) e redundância de processos.
- Aula 3 (MC714): mascarar falhas por redundância, disponibilidade e MTTR.
- Aula 14 (MC714): RPC síncrona, em que o cliente fica bloqueado até a resposta do servidor.
