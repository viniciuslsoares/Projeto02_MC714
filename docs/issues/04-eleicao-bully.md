# [04] Algoritmo 1: Eleição de líder (Bully)

**Labels:** `algoritmo`, `eleição`, `obrigatório` · **Sugestão de responsável:** Pessoa C · **Depende de:** #1, (#3 para integração com o detector)

## Objetivo
Implementar o algoritmo do Valentão (Bully) com mensagens `ELECTION`, `OK` e `COORDINATOR`.

## Tarefas
- [ ] Implementação com timeouts explícitos e IDs únicos.
- [ ] **Declaração do modelo** em `docs/modelo_bully.md`: modelo temporal (síncrono/parcialmente síncrono, justificar), canais, falhas (crash-stop e crash-recovery), reentrada de nó com ID maior, hipóteses de relógio e persistência.
- [ ] Propriedades de **safety** (no máximo um líder por época / todos os nós corretos concordam) e **liveness** (eventualmente um líder é eleito).
- [ ] Tratamento do retorno do nó de maior ID (nova eleição) e eleições concorrentes.
- [ ] Contador de mensagens por eleição para análise de complexidade (O(n²) no pior caso).
- [ ] Testes unitários + integração.

## Critérios de aceite
- Cenário normal: líder = maior ID vivo.
- Cenário de falha: líder cai ⇒ novo líder eleito e anunciado a todos.

