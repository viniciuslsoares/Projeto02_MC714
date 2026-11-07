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
   - Falhas de parada súbita (*crash-stop*) e religamento (*crash-recovery*) são orquestradas diretamente pelo Docker (`docker stop` e `docker start`).

---

## 2. Tipos de Falhas Suportadas

| Tipo de Falha | Mecanismo de Injeção | Efeito no Sistema | Recuperação |
|---|---|---|---|
| **Crash-Stop** | `docker stop mc714-nodeX` | Nó encerra imediatamente suas conexões e para de responder a requisições e pings. | Não se recupera no cenário. |
| **Crash-Recovery** | `docker start mc714-nodeX` | Nó volta a executar, carrega estado salvo em disco (`results/logs/state/`) e reintegra o cluster. | Volta ativo mantendo época/termo anterior. |
| **Partição de Rede** | `action: partition` via `/control` | Bloqueio bidirecional de mensagens entre nós especificados (ex: isola nó 3 de 1 e 2). Mensagens são descartadas e logadas (`drop, reason=partition`); como numa rede real, o emissor só percebe a falha quando o timeout da chamada expira. | `action: heal` desbloqueia o tráfego entre os nós. |
| **Perda de Mensagens** | `drop_prob` via `/control` | Perda nos dois sentidos, sorteada de forma independente com a mesma `drop_prob`: a requisição pode se perder (`drop, reason=loss`; o receptor nunca a recebe) ou a resposta pode se perder (`drop, reason=reply_loss`; o receptor já recebeu e processou a mensagem). Em ambos os casos o emissor só percebe a perda quando o timeout expira. | `action: reset_faults` restaura probabilidade a 0. |
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
