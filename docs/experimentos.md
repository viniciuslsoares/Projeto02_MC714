# Experimentos

Implementação: `experiments/grid.py` e `experiments/run_all.py` (issue #6). As tabelas e figuras saem dos CSVs na issue #9 (`analysis/analise.py`).

## 1. Como rodar

```bash
python -m experiments.run_all                               # todas as grades, 10 repetições (~23 min)
python -m experiments.run_all --grades nos --repeticoes 2   # rodada curta, para conferir
```

- Só usa a biblioteca padrão do Python (≥ 3.12). Não precisa de Docker nem de `pip install`.
- Saída, versionada no git (os dados brutos):
  - `results/experimentos/<grade>.csv`, com uma linha por ensaio;
  - `results/experimentos/<grade>.csv.meta.json`, com a proveniência (§6).
- Os dois arquivos são gravados de forma atômica (arquivo temporário + `os.replace`) no fim de cada grade.
  - Se um ensaio falhar, ou o `run_all` receber Ctrl+C ou SIGTERM, os nós são parados e os ensaios que já rodaram são gravados.
  - O campo `completa` do `.meta.json` diz se a grade terminou.
- Os logs JSONL de cada ensaio ficam em `results/raw/experimentos/<grade>/<parâmetro>=<nível>_seed<semente>/`, fora do git. Eles podem ser conferidos com `python -m analysis.log_verifier <pasta>`.

## 2. Ambiente de execução

- Cada nó roda num processo independente (`python -m src.node`, o mesmo comando do contêiner) e só se comunica por HTTP em `127.0.0.1`.
- Os experimentos não usam o compose. Subir e derrubar processos é mais rápido que contêineres e não depende de Docker, então todas as grades rodam com um comando em qualquer máquina com Python.
- O compose continua sendo o ambiente dos cenários (`scripts/run_scenario.sh`) e da demonstração.

## 3. Roteiro de um ensaio

1. Sobe os n nós com detector e Bully ligados.
2. Espera todos terem o nó n como líder (até 60 s; se não acontecer, a grade para com erro) e mais 2 s de aquecimento, fora das medidas.
3. Mede 4 s sem falha.
4. Derruba o líder (nó n) com SIGKILL e mede mais 10 s.
5. Para os outros nós com SIGTERM.

- Cada ensaio dura cerca de 17 s, e a duração real fica na coluna `duracao_s`.
- O instante do crash e os de início e fim das medidas vêm de `time.time()` no `run_all`. Os logs usam o mesmo relógio, porque tudo roda na mesma máquina.
- As repetições ficam por fora: primeiro a repetição 1 de todos os níveis, depois a 2, e assim por diante. Assim, uma mudança de carga na máquina não cai toda num nível só.
- Sementes: a repetição r usa `SEED = r`, igual em todos os níveis.
  - A semente só sorteia as perdas.
  - Sem perda, a variação entre repetições vem do escalonamento dos processos e threads e da fase do crash em relação aos heartbeats.

## 4. Grades

| Grade | Parâmetro variado | Níveis | Fixo |
|---|---|---|---|
| `nos` | número de nós `n` | 3, 5, 7, 9 | sem perda |
| `perda` | `drop_prob` (`NET_DROP_PROB`) | 0; 0,1; 0,2; 0,3 | n = 5 |

- Fixos nas duas grades: `HB_INTERVAL` = 0,5 s, `HB_TIMEOUT` = 2,0 s e `RPC_TIMEOUT` = 1,0 s, os padrões do compose.
- Não há atraso nem duplicação, pelas limitações de `docs/modelo_falhas.md` §7: o atraso não conta no timeout, e duplicação não é combinada com perda.
- Por que esses parâmetros:
  - `n` mostra o custo O(n²) em mensagens dos dois algoritmos.
  - A perda é a falha que causa falsos positivos no detector e eleições extras no Bully.

## 5. Métricas (colunas do CSV)

| Coluna | Algoritmo | Definição, a partir dos logs | Esperado |
|---|---|---|---|
| `deteccao_s` | detector | Tempo do crash até o primeiro `suspect` do nó n em cada sobrevivente, separados por `;` (n − 1 amostras por ensaio). Vazio se não houve `suspect` depois do crash: o sobrevivente não suspeitou, ou já suspeitava antes por um falso positivo (que entra em `falsos_positivos`). | Sem perda, entre `timeout − intervalo` e `timeout + intervalo` (1,5 a 2,5 s), sem depender de n (`docs/modelo_detector.md` §3). Com perda pode ser menor: heartbeats perdidos antes do crash adiantam a suspeita. |
| `deteccao_max_s` | detector | O maior dos `deteccao_s`: até todos suspeitarem. | Até 2,5 s, mais o atraso de escalonamento. |
| `falsos_positivos`, `fp_por_par_min` | detector | `suspect` de um nó vivo durante as medidas. A taxa divide pelo tempo de observação de cada par (observador, peer) monitorado, em minutos; é a taxa de erros de Chen, Toueg e Aguilera (2002). | 0 sem perda. Com perda p, cerca de pᵏ por janela, com k = 4. |
| `hb_msgs_por_s` | detector | Heartbeats enviados por segundo no sistema antes do crash (`send` mais `drop` por perda, com `msg="heartbeat"`). | n(n − 1) / intervalo: 12, 40, 84 e 144 para n = 3, 5, 7, 9. |
| `convergencia_s` | Bully | Tempo do crash até o primeiro instante em que todos os sobreviventes têm o nó n − 1 como líder. Vazio se não convergiu nos 10 s. | Sem perda, no máximo `timeout + intervalo + 2 × RPC_TIMEOUT` = 4,5 s (`docs/modelo_bully.md` §3); o verificador cobra 5,0 s, com a folga de 0,5 s. |
| `eleicao_s` | Bully | Tempo da primeira suspeita até a convergência: só o Bully, sem o detector. | Sem perda, poucos ms aqui (§7). Com perda pode ser negativo: a convergência veio de uma eleição causada por um falso positivo, antes de algum sobrevivente suspeitar do crash. |
| `eleicoes`, `msgs_election`, `msgs_ok`, `msgs_coordinator`, `msgs_bully` | Bully | Soma dos `msgs` dos `election_end` das eleições começadas entre o crash e a estabilização (a convergência e todas as detecções). Inclui as rodadas extras do novo líder (`docs/modelo_bully.md` §3). | Uma rodada: de 1 + (n − 1), no melhor caso, até n(n − 1)/2 + (n − 1)(n − 2)/2 + (n − 1) = n(n − 1), no pior. As rodadas extras somam mais. |
| `avisos_verificador` | os dois | Propriedades com `[AVISO]` no `analysis.log_verifier` sobre os logs do ensaio, separadas por `;`. | Vazio sem perda. Com perda, um `[AVISO]` pode ser esperado (`docs/testes.md` §3). |

- As outras colunas registram a configuração do ensaio: `grade`, `repeticao`, `seed`, `n`, `drop_prob`, `hb_intervalo`, `hb_timeout`, `rpc_timeout`, `aquecimento_s` e `duracao_s`.
- O `.meta.json` também traz a descrição de cada coluna.
- Percentis: os tempos de detecção e de convergência são as latências dos experimentos.
  - Detecção: com 10 repetições, cada nível tem de 20 (n = 3) a 80 (n = 9) amostras.
    - Mas as n − 1 amostras de um ensaio não são independentes: o crash é o mesmo, e os detectores costumam verificar quase na mesma fase.
    - O `deteccao_max_s` tem uma amostra independente por ensaio.
  - Convergência: tem uma amostra por ensaio, 10 por nível; com isso, o p95 e o p99 ficam praticamente iguais ao máximo.

## 6. Metadados (`<grade>.csv.meta.json`)

- `comando` que gerou o CSV.
- `completa`: se todos os ensaios da grade rodaram (§1).
- `git_commit` e `git_alteracoes_locais`.
  - `git_alteracoes_locais` diz se havia arquivos alterados ou novos fora de `results/`, que muda a cada execução.
  - Para os dados versionados, o `run_all` roda sobre um commit sem alterações locais.
- `inicio_utc`, `fim_utc` e `duracao_total_s`.
- `config`: a grade, com parâmetro, níveis, repetições, primeira semente e tempos do ensaio.
- `parametros_padrao` e `ensaio`: aquecimento, tempo antes e depois do crash, e a falha injetada.
- `ambiente`:
  - SO, arquitetura, modelo da CPU, número de CPUs e memória;
  - versão do Python e modo de execução.
- `colunas`: a descrição de cada coluna do CSV.
- Não grava o nome da máquina nem o do usuário.

## 7. Limitações

- **Processos, não contêineres.** Um envio para o nó derrubado falha na hora (conexão recusada), por isso `eleicao_s` fica em poucos ms. No compose, dependendo de como a rede do Docker responde, o `ELECTION` para o líder caído pode esperar até o `RPC_TIMEOUT`. Isso aumentaria `eleicao_s` e `convergencia_s`, mas não a detecção.
- **Uma só máquina.** Os nós não são domínios de falha independentes e disputam a mesma CPU. Com n = 9, são 9 processos e 144 heartbeats/s, e o atraso de escalonamento que isso causa entra nas medidas.
- **Início da detecção.** O tempo de detecção conta a partir do SIGKILL. O último heartbeat do nó derrubado chegou até um `intervalo` antes.
- **Parâmetros não variados.** Não há atraso nem duplicação (§4).
- **Poucas amostras de convergência.** Os percentis altos da convergência dependem de poucas amostras (§5).
- **Ensaios que não convergem.** Neles, a janela das mensagens do Bully vai até o fim do ensaio. Uma eleição ainda aberta quando os nós param não grava o `election_end`, e as mensagens dela ficam de fora.
