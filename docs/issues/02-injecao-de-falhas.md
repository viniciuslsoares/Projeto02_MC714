# [02] Framework de injeção de falhas e cenários reprodutíveis

**Labels:** `infra`, `falhas`, `obrigatório` · **Sugestão de responsável:** Pessoa A · **Depende de:** #1

## Objetivo
Produzir falhas e condições de rede repetíveis (scripts + sementes registradas).

## Tarefas
- [ ] Decidir e documentar a técnica: nível de aplicação (drop/delay/duplicação configuráveis) e/ou Toxiproxy / `tc netem`.
- [ ] Crash-stop e crash-recovery de nós (`docker stop/start/kill` via script, com persistência de estado quando aplicável).
- [ ] Perda, atraso, duplicação e partição de rede entre grupos de nós.
- [ ] Descrição declarativa de cenário (YAML: eventos × tempo × seed).
- [ ] Runner `scripts/run_scenario` que executa o cenário e salva logs em `results/raw/`.
- [ ] Documentar a limitação: contêineres numa só máquina não são domínios de falha independentes.

## Critérios de aceite
- Mesmo cenário + mesma seed ⇒ mesma sequência de injeções.
- Cenários de falha reutilizáveis pelas issues #3–#8.

