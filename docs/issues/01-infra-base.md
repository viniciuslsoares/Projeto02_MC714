# [01] Infraestrutura base: esqueleto, nó genérico, Docker Compose e comunicação

**Labels:** `infra`, `obrigatório` · **Sugestão de responsável:** Pessoa A · **Depende de:** —

## Objetivo
Criar o esqueleto do projeto que os demais algoritmos usam (ver `skills.md`, seções 3 e 8).

## Tarefas
- [ ] Estrutura de diretórios (`src/`, `tests/`, `experiments/`, `analysis/`, `docs/`, `results/`).
- [ ] Nó lógico genérico (processo/contêiner independente, config via env/arquivo: id, peers, portas, seed).
- [ ] Camada de comunicação exclusivamente via rede (gRPC ou HTTP/TCP) com **deadlines/timeouts explícitos**.
- [ ] `Dockerfile` + `compose.yaml` (≥ 3 nós, N configurável).
- [ ] Log estruturado (JSON) por nó com timestamp, id, evento, relógio lógico — base para verificação de logs.
- [ ] Comandos de ciclo de vida documentados (subir / testar / derrubar) no README (`Makefile` ou scripts).
- [ ] Configuração de CI mínima (lint + pytest).

## Critérios de aceite
- `docker compose up` sobe N nós que trocam mensagens de teste (ping).
- Nenhuma chamada de rede sem timeout.
- Logs parseáveis por script.

