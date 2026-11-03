# [06] Experimentos automatizados e métricas de desempenho

**Labels:** `experimentos`, `obrigatório` · **Sugestão de responsável:** Pessoa B · **Depende de:** #2, #3, #4

## Objetivo
Análise experimental obrigatória para cada algoritmo (25% da nota).

## Tarefas
- [ ] `experiments/grid.py`: `execute_grid(GridConfig) -> GridResults`, escrita atômica de CSV + `.csv.meta.json` (commit, comando, versões, config).
- [ ] Métricas por algoritmo:
  - Detector: tempo de detecção, taxa de falsos positivos, mensagens/s.
  - Bully: tempo de convergência da eleição, nº de mensagens.
- [ ] Variar ≥ 1 parâmetro em ≥ 3 níveis (nº de nós, perda/atraso, intervalo de heartbeat).
- [ ] ≥ 5-10 repetições independentes por configuração (sementes registradas).
- [ ] Registrar duração, warm-up, hardware, SO, versões e configuração.
- [ ] `experiments/run_all.py` como porta de entrada única.

## Critérios de aceite
- Um comando reproduz todos os CSVs brutos.
- Metadados de proveniência gravados.

