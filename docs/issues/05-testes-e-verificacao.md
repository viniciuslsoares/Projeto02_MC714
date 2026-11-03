# [05] Testes automatizados e verificador de logs (safety/liveness)

**Labels:** `testes`, `obrigatório` · **Sugestão de responsável:** Pessoa C · **Depende de:** #3, #4 (começar o esqueleto cedo, com #1)

## Objetivo
Demonstrar as propriedades de correção por testes automatizados e verificações reprodutíveis de logs (30% da nota).

## Tarefas
- [ ] Estratégia em 3 camadas (`skills.md`, seção 5): unitários, integração (compose) e falhas.
- [ ] `analysis/log_verifier.py`: lê logs JSON e verifica safety/liveness do Bully e do detector, gerando `results/verifications.txt` (`[OK]`/`[AVISO]`).
- [ ] Teste de sanidade ponta a ponta rápido (`test_sanity`).
- [ ] Testes de cenário normal e de falha para **cada** algoritmo.
- [ ] Execução no CI.

## Critérios de aceite
- `pytest` passa localmente e no CI.
- Violação deliberada de uma propriedade é detectada pelo verificador (teste do verificador).

