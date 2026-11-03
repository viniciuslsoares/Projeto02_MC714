# [08] Extra: integração dos algoritmos em um problema prático coerente

**Labels:** `bônus`, `integração` · **Sugestão de responsável:** Pessoa C · **Depende de:** #3, #4

## Objetivo
Ponto extra: "integração dos algoritmos para resolver um problema prático coerente".

## Proposta
**Serviço de coordenação com líder** (ex.: despachante de tarefas / agendador): o detector de falhas (#3) monitora os membros; o Bully (#4) elege o coordenador; o coordenador distribui tarefas aos workers vivos e as redistribui quando um worker é suspeito. Ao cair o líder, uma nova eleição ocorre e o serviço continua. (Tema pode ser ajustado pelo grupo.)

## Tarefas
- [ ] Definir o problema e requisitos (docs/integracao.md), incluindo o modelo do sistema combinado.
- [ ] Implementar o serviço sobre as APIs dos módulos existentes (sem duplicar algoritmos).
- [ ] Cliente/gerador de carga.
- [ ] Propriedades da aplicação (ex.: toda tarefa é executada, nenhuma tarefa atribuída por dois líderes simultaneamente) verificadas via logs.
- [ ] Cenário normal + cenário de falha (queda de worker e de líder) + métrica da aplicação (vazão, latência p50/p95/p99, tarefas reatribuídas).
- [ ] Incluir no `compose.yaml` e no `run_all`.

## Critérios de aceite
- Demonstração ponta a ponta (candidata ao vídeo) com falha injetada e recuperação.

