# [07] Extra: cenários de recuperação, partição e falhas combinadas

**Labels:** `bônus`, `falhas`, `experimentos` · **Sugestão de responsável:** Pessoa A · **Depende de:** #2, #3, #4

## Objetivo
Ponto extra do enunciado: "cenários adicionais de recuperação, partição ou falhas combinadas".

## Tarefas
- [ ] Crash-recovery: nó (inclusive o líder) reinicia e reintegra o grupo; medir tempo de recuperação.
- [ ] Partição de rede (split-brain no Bully: dois líderes?) e cura da partição: analisar e documentar o comportamento, medir tempo de reconvergência.
- [ ] Falhas combinadas: ex. líder cai durante partição + perda de mensagens; falhas em cascata.
- [ ] Cenários em YAML reutilizando o framework da #2, com seeds registradas.
- [ ] Estender o `log_verifier` (#5) para as propriedades sob esses cenários (o que se mantém / o que é violado e por quê).
- [ ] Resultados e análise (tabelas/figuras) para o relatório.

## Critérios de aceite
- ≥ 3 cenários novos (recuperação, partição, combinado) automatizados e repetíveis.
- Discussão explícita de limitações (hipóteses violadas).

