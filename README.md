# Projeto 02 — MC714: Algoritmos Distribuídos

Nós lógicos em Python (HTTP/JSON, usando só a biblioteca padrão) orquestrados por Docker Compose v2.
Algoritmos planejados: eleição **Bully** + detector de falhas por **heartbeat**, com extras de
falhas combinadas/partições e integração em um serviço prático. Planejamento em `docs/issues/`.

## Requisitos
Docker + Docker Compose v2, Python ≥ 3.12 (testes), bash (WSL/Git Bash no Windows).

## Comandos
```bash
./scripts/env.sh build        # constrói a imagem
./scripts/env.sh up           # sobe 3 nós (nodes 1..3) e espera ficarem saudáveis
./scripts/env.sh up5          # sobe 5 nós (perfil n5)
./scripts/env.sh ps           # estado dos contêineres
./scripts/env.sh logs         # logs JSON dos nós
./scripts/env.sh down         # remove contêineres, rede e volumes
./scripts/env.sh smoke        # sobe, confere pings entre nós e derruba
./scripts/run_scenario.sh scenarios/particao_rede.yaml  # executa cenário declarativo de falha
python -m pip install -r requirements.txt
python -m pytest              # testes automatizados (docs/testes.md)
python -m analysis.log_verifier [pasta]  # verifica safety/liveness nos logs -> results/verifications.txt
python -m experiments.run_all  # roda todos os experimentos, cenários e a integração -> results/experimentos/*.csv (docs/experimentos.md)
python -m experiments.cenarios # só os cenários de recuperação, partição e falhas combinadas (docs/cenarios.md)
python -m experiments.integracao  # só o serviço de tarefas com carga, normal e com quedas (docs/integracao.md)
COMPOSE_PROFILES=integracao ./scripts/run_scenario.sh scenarios/integracao/quedas.yaml  # o serviço de tarefas no compose
```
Logs estruturados (JSONL, um arquivo por nó) ficam em `results/logs/node<ID>.jsonl`.
Resultados de cenários com metadados ficam em `results/raw/<cenario>_seed<SEED>/`.
Dados brutos dos experimentos (versionados): `results/experimentos/<grade>.csv` e `<grade>.csv.meta.json`.

## Arquitetura e Falhas (issues #1 a #8)
| Arquivo | Papel |
|---|---|
| `src/config.py` | Configuração via variáveis de ambiente (id, peers, porta, seed, timeouts, falhas de rede) |
| `src/models.py` | `Message` (mensagem trocada entre os nós) |
| `src/eventlog.py` | Relógio de Lamport + log JSONL thread-safe; após reinício o relógio é retomado do maior `clock` do log (não volta a zero) |
| `src/network.py` | Transporte HTTP/JSON com **timeout obrigatório**; perda, atraso, duplicação e partição injetáveis (RNG semeado) |
| `src/node.py` | Nó genérico: servidor HTTP, rotas `/msg`, `/control` (falhas dinâmicas), persistência de estado e healthcheck |
| `experiments/fault_injector.py` | Injetor programático que lê cenários em YAML e comanda partições, atrasos e crashes |
| `scenarios/` | Cenários declarativos reproduzíveis: `particao_rede.yaml` e `queda_e_recuperacao.yaml` (#2); `cascata_e_recuperacao.yaml`, `particao_menor_id.yaml` e `particao_queda_e_perda.yaml` (#7) |
| `scripts/run_scenario.sh` | Runner automatizado para subir cluster, rodar cenário, coletar logs e salvar metadados |
| `docs/modelo_falhas.md` | Documentação formal das hipóteses de falhas, técnica adotada e limitações do ambiente |
| `src/detector.py` | Detector de falhas por heartbeat (#3): suspeita/restauração e callbacks `on_suspect`/`on_restore`; modelo em `docs/modelo_detector.md` |
| `src/bully.py` | Eleição de líder pelo Bully (#4): `ELECTION`/`OK`/`COORDINATOR`, usa o detector para saber se o líder caiu; líder atual em `GET /status`; modelo em `docs/modelo_bully.md` |
| `analysis/log_verifier.py` | Verificador de logs (#5): confere segurança e vivacidade do detector e do Bully e escreve `[OK]`/`[AVISO]` por propriedade; o `run_scenario.sh` roda ele no fim de cada cenário |
| `docs/testes.md` | Estratégia de testes em 3 camadas, cenário normal e de falha de cada algoritmo, propriedades conferidas pelo verificador |
| `experiments/grid.py` | Grade de experimentos (#6): `execute_grid(GridConfig)` roda ensaios com um processo por nó, derruba o líder e mede detecção, falsos positivos, heartbeats/s, convergência e mensagens do Bully; grava CSV + `.csv.meta.json` |
| `experiments/run_all.py` | Porta de entrada única dos experimentos: grades `nos` (n = 3, 5, 7, 9) e `perda` (0 a 0,3), cenários de falha (#7) e serviço de tarefas (#8) |
| `docs/experimentos.md` | Roteiro de um ensaio, grades, definição de cada métrica, metadados e limitações |
| `experiments/cenarios.py` | Cenários de falha (#7): roda cada `scenarios/*.yaml` com o injetor da #2 e um processo por nó; mede reconvergência depois de cada evento, tempo de recuperação e split-brain (nós que se acham líderes ao mesmo tempo); grava `results/experimentos/cenarios.csv` + `.meta.json` |
| `docs/cenarios.md` | Cenários de recuperação, partição e falhas combinadas: métricas, resultados, o que se mantém e o que é violado, limitações |
| `src/tarefas.py` | Serviço de tarefas (#8): o líder do Bully distribui as tarefas, em rodízio, aos workers que o detector vê vivos e as reatribui quando um worker falha; o worker só executa para o próprio líder |
| `src/cliente.py` | Cliente e gerador de carga (carga aberta): repete cada tarefa até a confirmação e procura o novo líder quando o atual cai; serviço `cliente` do compose (perfil `integracao`) |
| `experiments/integracao.py` | Roda `scenarios/integracao/*.yaml` (normal e com quedas de worker e de líder) com carga e mede vazão, latência p50/p95/p99, interrupção, tarefas reatribuídas e executadas duas vezes; grava `integracao.csv`, `integracao_tarefas.csv` + `.meta.json` |
| `docs/integracao.md` | Problema, requisitos, protocolo, modelo do sistema combinado, propriedades da aplicação, métricas, resultados e limitações |

Algoritmos registram handlers com `node.register("TIPO", fn)` e enviam com `node.net.send(peer, "TIPO", payload, timeout)`.

### Semântica do canal (base do modelo de sistema)
Cada mensagem é uma requisição HTTP independente (conexão TCP própria), enviada por várias threads e
com atraso injetado antes do envio: **não há FIFO** entre mensagens de um nó para o mesmo peer. Envio de
melhor esforço, sem retransmissão: perda (em cada sentido), partição e nó caído só aparecem como `None`
em `send()`; com duplicação, a mesma mensagem chega duas vezes. Sorteios por enlace, derivados da semente
do nó ⇒ reprodutíveis (com limites). Contêineres numa só máquina **não** são domínios de falha independentes.
Detalhes em [`docs/modelo_falhas.md`](docs/modelo_falhas.md) §6 (canal) e §7 (limitações conhecidas).

### Variáveis de ambiente
`NODE_ID, PEERS, BIND_ADDR, PORT, SEED, LOG_DIR, RPC_TIMEOUT, PING_INTERVAL, HB_INTERVAL, HB_TIMEOUT, NET_DROP_PROB,
NET_DELAY_MS, NET_JITTER_MS, NET_DUP_PROB` (detalhes em `src/config.py`). O `compose.yaml` aceita as mesmas via `.env`/shell.
`HB_INTERVAL > 0` liga o detector e o Bully (o compose usa 0,5 s); o Bully usa `RPC_TIMEOUT` como timeout do OK.
`BIND_ADDR` é `127.0.0.1` por padrão; no compose é `0.0.0.0` dentro da rede interna, e as portas são publicadas só em `127.0.0.1` (o `/control` não tem autenticação).
`SEED` é a semente do experimento (a registrada no `meta.json`); cada nó usa a sua própria, `SEED * 1000 + NODE_ID`, para que as falhas sorteadas sejam independentes entre nós.

## Reprodução completa e relatório
Testes e verificação: `docs/testes.md`. Experimentos, cenários e integração: `python -m experiments.run_all` refaz todos os CSVs brutos (`docs/experimentos.md`, `docs/cenarios.md`, `docs/integracao.md`). Relatório: issue #9.
