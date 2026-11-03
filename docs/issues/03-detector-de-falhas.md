# [03] Algoritmo 2: Detector de falhas por heartbeat

**Labels:** `algoritmo`, `detecção-de-falhas`, `obrigatório` · **Sugestão de responsável:** Pessoa B · **Depende de:** #1

## Objetivo
Implementar um detector de falhas baseado em heartbeat (ou gossip) próprio, sem biblioteca pronta (categoria "detecção de falhas").

## Tarefas
- [ ] Implementação (heartbeat periódico, timeout configurável, suspeita/restauração; opcional: timeout adaptativo).
- [ ] **Declaração do modelo do sistema** em `docs/modelo_detector.md`: modelo temporal (parcialmente síncrono), canais (perdas/atraso), falhas (crash-stop/recovery), relógios, persistência.
- [ ] Propriedades: completude (nó falho é eventualmente suspeito) e precisão (eventual; falsos positivos medidos).
- [ ] API para consumo por outros módulos (callbacks `on_suspect` / `on_restore`), usada pelo Bully (#4) e pela integração (#8).
- [ ] Testes unitários + integração (nó cai ⇒ detectado em ≤ limite).

## Critérios de aceite
- Cenário normal: nenhum falso positivo em rede estável.
- Cenário de falha: crash detectado por todos os nós corretos.

