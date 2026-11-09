# Histórico de iterações, decisões e melhorias

Registro cronológico para apoiar o relatório (seções de arquitetura, decisões, limitações e lições aprendidas).
**Convenção:** toda mudança relevante (decisão de projeto, bug encontrado, melhoria, resultado inesperado) ganha uma entrada nova no topo da seção "Iterações", com data, issue relacionada, motivo e impacto.

## Escopo e decisões iniciais
| Item | Decisão | Motivo |
|---|---|---|
| Algoritmo 1 | Eleição de líder (Bully) | Categoria exigida (eleição); simples de modelar e testar sob falhas |
| Algoritmo 2 | Detector de falhas por heartbeat | Categoria exigida (detecção de falhas); alimenta o Bully |
| Bônus escolhido | Cenários de recuperação/partição/falhas combinadas (#7) | Enunciado, "Itens Extras" |
| Bônus escolhido | Integração dos algoritmos em um problema prático (#8) | Enunciado, "Itens Extras" |
| Ambiente | Docker Compose v2, única máquina | Ambiente de referência do enunciado |
| Linguagem | Python 3.13 | Decisão do grupo; alinhada ao skills.md (pytest, matplotlib, análise em Python) |
| Comunicação | **HTTP/JSON** (decisão do grupo, em vez de gRPC) | Simples de depurar e de injetar falhas (Toxiproxy/aplicação); timeouts explícitos em toda chamada; nós só com biblioteca padrão (`http.server` + `urllib`) |
| Scripts | Sempre em bash (não PowerShell) | Decisão do grupo |

> Algoritmos propostos pelo assistente; ajuste aqui se o grupo mudar.

## Comandos de Teste e Validação

Guia de comandos utilizados para validar cada etapa da infraestrutura e dos algoritmos.

### 1. Testes Unitários e Locais (sem Docker)
Não exigem contêineres; rodam na máquina local ou no WSL usando portas dinâmicas de loopback:
```bash
# 1. Instalar dependências de teste e análise
python -m pip install -r requirements.txt

# 2. Executar toda a bateria de testes unitários
python -m pytest -q

# 3. Executar testes com detalhes de cada teste
python -m pytest -v tests/test_infra.py
```

### 2. Validação Automatizada de Fumaça (Smoke Test)
Sobe 3 nós lógicos via Docker Compose v2, aguarda que fiquem `healthy`, verifica se trocaram pings e desmonta o ambiente:
```bash
./scripts/env.sh smoke
```
*Critério de sucesso:* saída exibindo `replies de ping observadas: N` (com $N \ge 6$) e a mensagem `SMOKE OK`.

### 3. Validação Manual do Ciclo de Vida do Cluster
```bash
# Subir cluster padrão com 3 nós (node1, node2, node3)
./scripts/env.sh up

# Verificar status e healthcheck dos contêineres
./scripts/env.sh ps

# Acompanhar logs JSONL estruturados dos nós em tempo real
./scripts/env.sh logs -f

# Derrubar contêineres, redes e volumes
./scripts/env.sh down
```

### 4. Validação de Escala (5 nós — Perfil `n5`)
Garante que o cluster escala para 5 nós e que as variáveis de `PEERS` se ajustam sem falhas de conexão espúrias:
```bash
# Subir com 5 nós
./scripts/env.sh up5

# Verificar se não há 'send_failed' indevido
./scripts/env.sh logs | grep -c send_failed   # esperado: 0

# Derrubar
./scripts/env.sh down
```

### 5. Teste de Injeção de Falhas de Rede e Quedas
```bash
# Iniciar cluster simulando 20% de perda e 50ms de atraso
NET_DROP_PROB=0.2 NET_DELAY_MS=50 ./scripts/env.sh up

# Simular crash-stop manual de um nó
docker stop mc714-node2

# Inspecionar detecção de erro nos logs dos outros nós
./scripts/env.sh logs node1

# Simular recuperação (crash-recovery)
docker start mc714-node2

# Limpeza
./scripts/env.sh down
```

### 6. Execução Automatizada de Cenários de Falhas
Executa o ciclo completo de um cenário YAML (subida do cluster, injeção temporal de falhas, coleta de logs em `results/raw/` e encerramento):
```bash
# Executa cenário com a semente definida no YAML
./scripts/run_scenario.sh scenarios/particao_rede.yaml

# Executa cenário sobrescrevendo a semente pseudoaleatória
./scripts/run_scenario.sh scenarios/queda_e_recuperacao.yaml 1002
```

*Os logs estruturados para auditoria são persistidos em:* `results/logs/node<ID>.jsonl`.

## Iterações
### 2026-11-09: Issue #9 (Etapa 1) - Script de consolidação estatística, figuras e tabelas LaTeX (analysis/analise.py)
- **Objetivo:** agregar os dados brutos versionados em `results/experimentos/`, calcular medidas de tendência central e variabilidade (média $\pm$ IC95%, desvio padrão, mediana, p50, p95, p99) e gerar figuras e tabelas prontas para o relatório IEEE (requisito obrigatório do enunciado).
- **Entregue:**
  - `analysis/analise.py`: script completo que processa `nos.csv`, `perda.csv`, `cenarios.csv`, `integracao.csv` e `integracao_tarefas.csv`.
  - Tabelas LaTeX em `results/analise/tabelas/`: `tab_grade_nos.tex`, `tab_grade_perda.tex`, `tab_cenarios_falha.tex`, `tab_integracao_servico.tex` formatadas com booktabs.
  - Figuras vetoriais (PDF) e rasterizadas (PNG a 300 DPI) em `results/analise/figuras/`:
    - `fig1_complexidade_mensagens`: mensagens do Bully vs $N$ confrontadas com pior caso teórico $O(N^2)$ e heartbeats/s.
    - `fig2_tempos_convergencia_nos`: estabilidade dos tempos de detecção e convergência do Bully vs $N$.
    - `fig3_impacto_perda`: detecção, convergência, falsos positivos e eleições espúrias sob perda de pacotes.
    - `fig4_integracao_latencia_cdf`: CDF comparativa de latência do serviço de tarefas (normal vs quedas de workers e líder).
    - `fig5_cenarios_split_brain`: duração e tempos de split-brain nos 5 cenários declarativos.
  - `tests/test_analise.py`: 4 testes unitários e de integração validando funções estatísticas, formatação e geração dos artefatos.
- **Decisão de projeto:** gerar simultaneamente figuras em `.pdf` (para inclusão vetorial de alta qualidade no LaTeX do relatório) e `.png` (para visualização rápida no Markdown/GitHub).
### 2026-11-06: Issue #2 (Etapa 5) - Documentação do modelo de falhas e limitações do ambiente
- **Objetivo:** formalizar a técnica adotada, as hipóteses de falhas e as limitações de domínio de falha de contêineres em uma só máquina (requisito obrigatório do enunciado).
- **Entregue:**
  - `docs/modelo_falhas.md`: documentação completa cobrindo técnica de injeção (aplicação + Docker), tipos de falhas suportadas (crash-stop, crash-recovery, partição, perda, atraso, duplicação), estrutura de cenários YAML e justificativa das limitações físicas de contêineres (compartilhamento de kernel, CPU, memória e ausência de drift de relógio).
  - `README.md`: documentação dos novos comandos de execução de cenários e tabela de arquitetura atualizada para as issues #1 e #2.
- **Conclusão da Issue #2:** framework de injeção de falhas 100% implementado, testado (22 testes unitários passando) e pronto para ser reutilizado pelo detector de falhas (issue #3) e eleição Bully (issue #4).
### 2026-11-06: Issue #2 (Etapa 4) - Testes unitários do framework de falhas (test_faults.py)
- **Objetivo:** validar o comportamento do injetor de falhas, carregamento de YAML, partições bidirecionais, degradações e rastreabilidade nos logs sem dependência de Docker.
- **Entregue:**
  - `tests/test_faults.py`: 6 novos testes cobrindo validação de YAML, partição bidirecional com isolamento e cura, degradação de enlace, callbacks de crash/recovery e execução temporal de cenário completo com verificação dos eventos no log JSONL.
  - Total de **22 testes passando** na suíte `pytest`.
- **Decisão de projeto:** manter testes de falhas executáveis em processos locais viabiliza execução instantânea e determinística no pipeline de CI do GitHub Actions, reservando o Docker para validações de integração e experimentos ponta a ponta.
### 2026-11-06: Issue #2 (Etapa 3) - Runner de cenários em bash (run_scenario.sh)
- **Objetivo:** orquestrar a execução de cenários declarativos em contêineres Docker e armazenar os logs com proveniência e metadados.
- **Entregue:**
  - `scripts/run_scenario.sh`: script bash que valida o cenário YAML, sobe o cluster Docker, dispara o injetor de falhas, coleta os logs de todos os nós para `results/raw/<cenario>_seed<SEED>/`, gera metadados em `meta.json` (commit, seed, timestamp) e derruba os contêineres.
  - `.gitignore`: adicionado `results/raw/` para evitar versionar traces brutos de testes locais.
- **Decisão de projeto:** isolar cada execução em uma pasta própria com `meta.json` garante reprodutibilidade estrita para a seção de avaliação experimental do relatório (requisito do enunciado).
### 2026-11-06: Issue #2 (Etapa 2) - Injetor de falhas em Python e cenários em YAML
- **Objetivo:** criar a especificação declarativa de cenários de teste e o executor programático de injeção de falhas com base em tempo e semente.
- **Entregue:**
  - `requirements.txt`: adição de `pyyaml>=6.0` para leitura declarativa de cenários.
  - `scenarios/particao_rede.yaml`: cenário reproduzível isolando temporariamente o nó 3 dos nós 1 e 2.
  - `scenarios/queda_e_recuperacao.yaml`: cenário com crash-stop no nó 2, atraso temporário no nó 1 e subsequente crash-recovery do nó 2.
  - `experiments/fault_injector.py`: classe `FaultInjector` e função `carregar_cenario()` com suporte a partição bidirecional (`partition`/`heal`), degradação (`set_faults`/`reset_faults`) e paradas/retomadas (`crash`/`recover` via Docker ou callbacks).
  - `compose.yaml`: mapeamento de portas dos nós (8001..8005) para acesso direto do injetor aos endpoints de controle via localhost.
- **Decisão de projeto:** a descrição em YAML isola a especificação dos experimentos do código dos nós, permitindo reutilizar os mesmos cenários de falhas para testar o detector (issue #3), o Bully (issue #4) e as partições complexas (issue #7).
### 2026-11-06: Início da issue #2 - Endpoint de controle e persistência de estado
- **Objetivo:** permitir que o framework de falhas controle dinamicamente partições e degradação de enlace via HTTP (`POST /control`), além de suporte a salvar/carregar estado para crash-recovery.
- **Entregue:**
  - `src/network.py`: métodos `reset_faults()`, `get_status()`, e emissão de eventos no log (`faults_updated`, `faults_reset`, `partition_start`, `partition_end`).
  - `src/node.py`: endpoint `POST /control` suportando ações `block`, `unblock`, `set_faults`, `reset_faults` e `status`. Métodos `salvar_estado()` e `carregar_estado()` salvando JSON simples em `results/logs/state/`. Endpoint `GET /status` adicionado.
  - `tests/test_infra.py`: novos testes `test_endpoint_controle_bloqueio_e_falhas` e `test_persistencia_de_estado_do_no`. Total de 16 testes passando.
- **Decisão de projeto:** manter o endpoint de controle no próprio nó evita dependência de ferramentas pesadas de terceiros (como Toxiproxy ou iptables) e funciona igualmente em testes unitários locais e em contêineres Docker.
### 2026-11-03: Simplificação do código da infra (issue #1)
- **Motivo:** deixar o código mais direto e fácil de explicar/defender pelos três integrantes (o uso de IA no desenvolvimento será declarado no relatório).
- **Mudanças (sem alterar comportamento):** `src/` reescrito em estilo mais simples: funções e variáveis em português, sem `frozen`/anotações de tipo elaboradas, sem a classe `LogEvent` (o log monta um dict direto), `Network.send` dividido em passos lineares (partição, perda, atraso, duplicação, envio) e comentários explicando o motivo de cada decisão (timeout, lock, semente).
- **Renomeações visíveis nos testes:** `clock.value` → `clock.valor`; `parse_peers(self_id=)` → `parse_peers(meu_id=)`.
- **Verificação:** mesmos 14 testes passam; smoke com Docker Compose reverificado.
- **Lição:** a melhor forma de garantir que dá para defender o código é conseguir explicar o fluxo de uma mensagem do `send` até o handler; ver `docs/arquitetura.md` (a escrever).
### 2026-11-03: Issue #1 implementada (infra base)
- **Decisão:** HTTP/JSON em vez de gRPC. Nós só com stdlib Python; ferramentas de teste/análise em `requirements.txt`.
- **Entregue:** `src/{config,models,eventlog,network,node}.py`, `Dockerfile`, `compose.yaml` (3 nós; perfil `n5` adiciona nós 4-5), `scripts/env.sh` (build/up/up5/ps/logs/down/smoke), `tests/test_infra.py`, CI (`.github/workflows/ci.yml`), README com comandos.
- **Decisões de projeto:**
  - Timeout obrigatório em todo `send` (`timeout<=0` levanta erro); `send` é best-effort e devolve `None` em falha.
  - Falhas de rede injetáveis no lado emissor (perda, atraso+jitter, duplicação, partição via `block`) com RNG semeado por nó (mesma seed ⇒ mesma sequência de perdas). Antecipa parte da issue #2.
  - Relógio de Lamport em todas as mensagens e eventos de log; log JSONL por nó em `results/logs/` (base do `log_verifier` da issue #5).
  - `compose.yaml` configurável por variáveis de ambiente (mesma imagem em qualquer ambiente, como exige o enunciado para a nuvem do IC).
- **Verificação:** 14 testes `pytest` passam (config, ping, Lamport, timeout contra peer mudo, peer inexistente, partição/cura, perda reprodutível, atraso, duplicação, logs). Smoke com Docker Compose: 3 contêineres `healthy` e 6 respostas de ping entre nós.
- **Problemas/lições:**
  - O sandbox do agente bloqueia sockets (inclusive loopback, erro 10013), então os testes de rede foram rodados fora dele; não é problema do código.
  - 1º run: 1 teste falhou por expectativa errada (nó desconhecido responde `type=error`, não `ok`); corrigido o teste, não o código.
  - A integração Docker Desktop ↔ Ubuntu (WSL) ainda não está ativa (Settings > Resources > WSL integration); `scripts/env.sh` foi testado em sintaxe (`bash -n`) e o equivalente rodado no Docker do Windows. Validar o script no Ubuntu após ativar.
- **Validação pelo grupo (16:58):** `./scripts/env.sh smoke` rodou no Ubuntu/WSL com a integração do Docker Desktop ativada: `SMOKE OK`, 3 nós `healthy`.
- **Bug encontrado nos logs:** o `PEERS` do compose sempre listava 5 nós; com 3 nós ativos, cada nó pingava os nós 4 e 5 inexistentes, gerando `send_failed` espúrios (que seriam lidos como falhas pelo detector/Bully). **Correção:** `PEERS` agora é configurável (padrão 1..3) e `env.sh up5` passa os 5 peers. Reverificado: 3 nós = 0 `send_failed`, 38 `reply`; 5 nós = 0 `send_failed`.
- **Limitações a citar no relatório:** contêineres numa só máquina não são domínios de falha independentes; `HEALTHCHECK` e `ping` são só infraestrutura, não o detector de falhas (issue #3).
### 2026-11-03: Início da issue #1 (infra base)
- Decisões: linguagem Python; scripts em bash; Docker é obrigatório (ambiente de referência do enunciado = Docker Compose v2).
- Script de publicação das issues reescrito de PowerShell para bash (`scripts/create_issues.sh`).
- Verificação do ambiente do desenvolvedor (Windows): **Docker, Podman e WSL ausentes**; Python 3.13.6 e Git 2.50.1 presentes. Necessário instalar WSL2 + Docker Desktop (exige admin e reinício) antes de testar o `compose.yaml`.
- Pendente: confirmar gRPC vs HTTP e instalar Docker em cada máquina do grupo.
- **Ambiente validado (16:44):** WSL2 + Ubuntu 26.04 LTS (Python 3.14.4, Git 2.53.0, 18 CPUs, 7,5 GiB RAM); Docker 29.9.2 e Docker Compose v5.6.1 (Docker Desktop) funcionando no Windows e no Ubuntu; `docker run hello-world` OK. Útil para a seção de ambiente/hardware do relatório.
### 2026-11-03: Planejamento
- Enunciado (`Trabalho2-MC714_2s2026.pdf`) e `skills.md` analisados.
- Projeto dividido em 9 issues (`docs/issues/`), para 3 pessoas:
  1 Infra base · 2 Injeção de falhas · 3 Detector de falhas · 4 Bully · 5 Testes/verificador · 6 Experimentos · 7 Extra: recuperação/partição · 8 Extra: integração · 9 Relatório/vídeo/entrega.
- Criado `scripts/create_issues.sh` para publicar as issues no GitHub.
- Observação: o `gh` local estava com token inválido; as issues precisam de `gh auth login`.
- Observações do enunciado: prazo 13/11/2026; o enunciado menciona dupla e GitLab da disciplina, enquanto o grupo tem 3 pessoas e usa GitHub (confirmar com o professor/PED e espelhar o repositório).

## Problemas encontrados / lições aprendidas
_(preencher durante o desenvolvimento)_

## Resultados preliminares e mudanças de rumo
_(preencher durante o desenvolvimento)_






