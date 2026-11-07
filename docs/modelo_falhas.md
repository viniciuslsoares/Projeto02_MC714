# Modelo de Falhas e Framework de Injeção

Este documento descreve as hipóteses de falha, a arquitetura do framework de injeção e a limitação do ambiente experimental adotado no Projeto 02 de MC714.

---

## 1. Técnica Adotada e Justificativa

Para permitir a investigação experimental do comportamento dos algoritmos distribuídos diante de falhas e variações de rede, adotamos uma abordagem híbrida:

1. **Falhas de Enlace no Nível da Aplicação (HTTP/JSON)**:
   - Implementadas diretamente na camada de rede (`src/network.py`) e controladas dinamicamente via endpoint administrativo (`POST /control` em `src/node.py`).
   - **Vantagens:** não requer privilégios elevados de kernel (`tc netem` ou `CAP_NET_ADMIN`), dispensa ferramentas extras de proxy intermediário (como Toxiproxy) e funciona de maneira 100% idêntica tanto em contêineres Docker quanto em testes locais rápidos com `pytest`.
   - **Reprodutibilidade:** todos os sorteios de perda, atraso e jitter utilizam um gerador pseudoaleatório próprio (`random.Random(seed)`), garantindo que a mesma semente gere rigorosamente a mesma sequência de eventos em qualquer máquina.

2. **Falhas de Nó via Ciclo de Vida de Contêineres (Docker)**:
   - Falhas de parada súbita (*crash-stop*) e religamento (*crash-recovery*) são orquestradas diretamente pelo Docker (`docker kill` e `docker start`).
   - `docker kill` envia SIGKILL ao processo do nó: ele morre na hora, sem executar código de encerramento e sem registrar `node_stop` no log, como num crash real. Não usamos `docker stop` porque ele envia SIGTERM, que o nó trata como desligamento gracioso (chama `stop()` e registra `node_stop`).
   - `docker start` cria um processo novo do zero: todo o estado em memória se perde e só sobrevive o que está em disco.

---

## 2. Tipos de Falhas Suportadas

| Tipo de Falha | Mecanismo de Injeção | Efeito no Sistema | Recuperação |
|---|---|---|---|
| **Crash-Stop** | `docker kill mc714-nodeX` (SIGKILL) | Processo do nó morre na hora, sem encerramento gracioso: não executa `stop()` nem registra `node_stop` no log. Para de responder a requisições e pings. | Não se recupera no cenário. |
| **Crash-Recovery** | `docker start mc714-nodeX` | Sobe um processo novo do zero; o estado em memória do processo anterior é perdido. Só sobrevive o que está em disco: os logs e o que o algoritmo gravou com `salvar_estado` (`results/logs/state/`), lido de volta com `carregar_estado`. | Volta a responder; o estado retomado (ex.: época/termo) depende de cada algoritmo usar a persistência. |
| **Partição de Rede** | `action: partition` via `/control` | Bloqueio bidirecional de mensagens entre nós especificados (ex: isola nó 3 de 1 e 2). Mensagens são descartadas e logadas (`drop, reason=partition`). | `action: heal` desbloqueia o tráfego entre os nós. |
| **Perda de Mensagens** | `drop_prob` via `/control` | Porcentagem configurável de mensagens é descartada silenciosamente no emissor (`drop, reason=loss`). | `action: reset_faults` restaura probabilidade a 0. |
| **Atraso e Jitter** | `delay_ms` e `jitter_ms` | Mensagens sofrem atraso base fixo + jitter uniforme antes do envio HTTP. | `action: reset_faults` restaura atraso a 0ms. |
| **Duplicação** | `dup_prob` via `/control` | Mensagens são transmitidas duas vezes consecutivas para testar idempotência. | `action: reset_faults` restaura duplicação a 0. |

---

## 3. Cenários Declarativos em YAML

Os cenários são especificados em arquivos declarativos na pasta `scenarios/`:
- `name`: identificador do cenário.
- `duration_s`: tempo total de observação em segundos.
- `seed`: semente pseudoaleatória padrão para garantir determinismo.
- `events`: lista de eventos ordenados por `time_s` (instante em segundos a partir do início).

Exemplo:
```yaml
name: "particao_rede"
duration_s: 12.0
seed: 1001

events:
  - time_s: 3.0
    action: "partition"
    nodes: [1, 2]
    target: 3

  - time_s: 8.0
    action: "heal"
    nodes: [1, 2]
    target: 3
```

---

## 4. Limitações do Ambiente Experimental

Conforme destacado no enunciado da disciplina, o ambiente de referência utiliza contêineres orquestrados por Docker Compose em um único computador. Essa abordagem possui limitações importantes que devem ser consideradas na análise experimental e no relatório:

1. **Ausência de Domínios de Falha Independentes**:
   - Os contêineres compartilham o mesmo hardware físico (CPU, barramento, controladora de disco e memória RAM) e o mesmo kernel do sistema operacional hospedeiro.
   - Uma falha catastrófica no hospedeiro (como falta de energia ou travamento de kernel) afeta todos os nós simultaneamente.

2. **Contenção e Interferência de Recursos**:
   - Atrasos de CPU e latências de disco induzidos por um contêiner podem desacelerar os nós vizinhos, criando falsos gargalos de rede ou oscilações de temporização.

3. **Relógio Físico Compartilhado**:
   - Como todos os contêineres leem o relógio do mesmo sistema operacional, não há deriva real de relógios físicos (*clock drift*). Por esse motivo, é essencial adotar relógios lógicos (como Lamport) para ordenação causal independente do relógio do sistema.

---

## 5. Como Executar Cenários de Falhas

Para executar um cenário com o cluster Docker ativo e gravar os resultados com rastreabilidade:

```bash
# Executa cenário de partição de rede
./scripts/run_scenario.sh scenarios/particao_rede.yaml

# Executa cenário de crash e recuperação com semente customizada
./scripts/run_scenario.sh scenarios/queda_e_recuperacao.yaml 2026
```

Os logs gerados de cada nó e o arquivo `meta.json` com os metadados de proveniência (commit Git, semente e timestamp) são armazenados em `results/raw/<cenario>_seed<SEED>/`.

---

## 6. Propriedades do canal

O transporte é HTTP/JSON sobre TCP (`src/network.py`, com `urllib`): cada `send()` faz uma requisição HTTP independente ao `/msg` do peer e espera a resposta até o timeout. Os sorteios de perda, atraso e duplicação usam um gerador pseudoaleatório por enlace, derivado da semente do nó; com a mesma semente, cada enlace repete a mesma sequência de falhas.

- **Ordenação:** não há FIFO entre mensagens distintas, nem do mesmo emissor para o mesmo peer. Cada mensagem usa uma conexão TCP própria e sai de uma entre várias threads (loop de ping, threads do servidor HTTP, threads dos algoritmos) depois de um atraso sorteado; o receptor atende cada requisição numa thread própria (`ThreadingHTTPServer`). Dentro de uma requisição o TCP entrega os bytes íntegros e em ordem, mas os algoritmos não podem assumir FIFO: se precisarem, numeram as mensagens e reordenam no receptor.
- **Perdas:** a perda simulada vale em cada sentido (requisição e resposta), com a mesma `drop_prob`; a partição descarta a mensagem no emissor. Nos dois casos o emissor só percebe quando o timeout expira (`send()` devolve `None` após `timeout`); se a perda foi da resposta, o receptor já processou a mensagem. O emissor não distingue perda da requisição, perda da resposta, partição ou nó caído: tudo vira timeout (ou erro de conexão imediato, quando o contêiner do peer não existe).
- **Duplicação:** com `dup_prob`, a mesma mensagem (mesmo timestamp de Lamport) é entregue duas vezes ao receptor. Uma retransmissão do algoritmo após timeout tem efeito parecido, porque resposta perdida não implica requisição perdida. Por isso os handlers precisam ser idempotentes.
- **Garantia de entrega:** nenhuma. O envio é de melhor esforço, com no máximo uma entrega por tentativa (duas com duplicação) e sem retransmissão automática; quem chama `send()` decide se tenta de novo. No sentido da Aula 14, a comunicação é **transiente** (a mensagem só existe enquanto emissor e receptor executam, sem a persistência de um middleware orientado a mensagens da Aula 15) e **síncrona bloqueante** (`send()` bloqueia até o receptor processar e responder, ou até o timeout).
- **Integridade:** supomos que não há corrupção nem mensagens forjadas (não consideramos falhas bizantinas). O TCP detecta corrupção de bytes, e só os nós e o injetor de falhas acessam `/msg` e `/control`; o campo `sender` é aceito sem autenticação.
