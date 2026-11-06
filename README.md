# Projeto 02 — MC714: Algoritmos Distribuídos

Nós lógicos em Python (HTTP/JSON, usando só a biblioteca padrão) orquestrados por Docker Compose v2.
Algoritmos planejados: eleição **Bully** + detector de falhas por **heartbeat**, com extras de
falhas combinadas/partições e integração em um serviço prático. Planejamento em `docs/issues/`;
decisões e iterações em [`HISTORICO.md`](HISTORICO.md).

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

## Arquitetura e Falhas (issues #1 e #2)
| Arquivo | Papel |
|---|---|
| `src/config.py` | Configuração via variáveis de ambiente (id, peers, porta, seed, timeouts, falhas de rede) |
| `src/models.py` | `Message` e `LogEvent` |
| `src/eventlog.py` | Relógio de Lamport + log JSONL thread-safe |
| `src/network.py` | Transporte HTTP/JSON com **timeout obrigatório**; perda, atraso, duplicação e partição injetáveis (RNG semeado) |
| `src/node.py` | Nó genérico: servidor HTTP, rotas `/msg`, `/control` (falhas dinâmicas), persistência de estado e healthcheck |
| `experiments/fault_injector.py` | Injetor programático que lê cenários em YAML e comanda partições, atrasos e crashes |
| `scenarios/` | Cenários declarativos reproduzíveis (`particao_rede.yaml`, `queda_e_recuperacao.yaml`) |
| `scripts/run_scenario.sh` | Runner automatizado para subir cluster, rodar cenário, coletar logs e salvar metadados |
| `docs/modelo_falhas.md` | Documentação formal das hipóteses de falhas, técnica adotada e limitações do ambiente |

Algoritmos registram handlers com `node.register("TIPO", fn)` e enviam com `node.net.send(peer, "TIPO", payload, timeout)`.

### Semântica do canal (base do modelo de sistema)
HTTP sobre TCP: sem reordenação/corrupção por conexão; envio *best-effort* (devolve `None` em
timeout/erro); perdas/atrasos/duplicações injetados por RNG semeado ⇒ reprodutíveis;
`block(peer)` simula partição. Contêineres numa só máquina **não** são domínios de falha independentes.

### Variáveis de ambiente
`NODE_ID, PEERS, PORT, SEED, LOG_DIR, RPC_TIMEOUT, PING_INTERVAL, NET_DROP_PROB, NET_DELAY_MS,
NET_JITTER_MS, NET_DUP_PROB` (detalhes em `src/config.py`). O `compose.yaml` aceita as mesmas via `.env`/shell.

## Reprodução completa e relatório
Serão adicionados conforme as issues #5, #6 e #9.
