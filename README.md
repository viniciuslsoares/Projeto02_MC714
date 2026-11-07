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
python -m pytest              # testes automatizados
```
Logs estruturados (JSONL, um arquivo por nó) ficam em `results/logs/node<ID>.jsonl`.
Resultados de cenários com metadados ficam em `results/raw/<cenario>_seed<SEED>/`.

## Arquitetura e Falhas (issues #1 a #4)
| Arquivo | Papel |
|---|---|
| `src/config.py` | Configuração via variáveis de ambiente (id, peers, porta, seed, timeouts, falhas de rede) |
| `src/models.py` | `Message` (mensagem trocada entre os nós) |
| `src/eventlog.py` | Relógio de Lamport + log JSONL thread-safe; após reinício o relógio é retomado do maior `clock` do log (não volta a zero) |
| `src/network.py` | Transporte HTTP/JSON com **timeout obrigatório**; perda, atraso, duplicação e partição injetáveis (RNG semeado) |
| `src/node.py` | Nó genérico: servidor HTTP, rotas `/msg`, `/control` (falhas dinâmicas), persistência de estado e healthcheck |
| `experiments/fault_injector.py` | Injetor programático que lê cenários em YAML e comanda partições, atrasos e crashes |
| `scenarios/` | Cenários declarativos reproduzíveis (`particao_rede.yaml`, `queda_e_recuperacao.yaml`) |
| `scripts/run_scenario.sh` | Runner automatizado para subir cluster, rodar cenário, coletar logs e salvar metadados |
| `docs/modelo_falhas.md` | Documentação formal das hipóteses de falhas, técnica adotada e limitações do ambiente |
| `src/detector.py` | Detector de falhas por heartbeat (#3): suspeita/restauração e callbacks `on_suspect`/`on_restore`; modelo em `docs/modelo_detector.md` |
| `src/bully.py` | Eleição de líder pelo Bully (#4): `ELECTION`/`OK`/`COORDINATOR`, usa o detector para saber se o líder caiu; líder atual em `GET /status`; modelo em `docs/modelo_bully.md` |

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
Serão adicionados conforme as issues #5, #6 e #9.
