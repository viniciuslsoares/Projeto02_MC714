# Testes e verificação dos logs

Implementação: `tests/` e `analysis/log_verifier.py` (issue #5). As propriedades verificadas são as de `docs/modelo_detector.md` §3 e `docs/modelo_bully.md` §3.

## 1. Estratégia em 3 camadas

- **Unitários:** funções e handlers chamados diretamente, sem threads de fundo.
  - `test_infra` (config, `Message`), `test_relogio`, `test_seed`, `test_rng_enlace`, `test_crash_docker` (comandos do injetor, sem Docker).
  - `test_faults::test_carregar_*` (cenários YAML).
  - `test_detector::test_suspeita_e_restauracao` e os unitários de `test_bully` (handlers e decisão de eleição com estado escolhido à mão).
  - `test_log_verifier::test_violacao_deliberada_e_detectada` (verificador com logs sintéticos).
  - `test_experimentos::test_medir_com_log_sintetico` (métricas dos experimentos com um log sintético).
- **Integração:** vários nós reais trocando mensagens HTTP.
  - No pytest (local e CI), com os nós em threads e portas livres em 127.0.0.1:
    - `test_sanity`;
    - `test_infra` (ping, Lamport, timeout, `/control`);
    - `test_handler_erro`;
    - `test_detector::test_rede_estavel_sem_falso_positivo`;
    - `test_bully::test_cenario_normal_lider_e_o_maior_id`.
    - `test_experimentos::test_grade_com_processos_reais` (uma grade pequena, com um processo por nó, grava o CSV e o `.meta.json`).
  - No compose (Docker, rodado à mão): `./scripts/env.sh smoke`; ou `./scripts/env.sh up` e, depois, `python -m analysis.log_verifier` sobre `results/logs`.
- **Falhas:** crash, perda, atraso, duplicação e partição injetados.
  - No pytest:
    - `test_detector::test_crash_detectado_por_todos_e_recuperacao`;
    - `test_bully::test_lider_cai_novo_lider_e_anunciado_e_maior_id_volta`;
    - `test_log_verifier::test_execucao_real_com_crash_e_retorno_sem_avisos`;
    - `test_perda`;
    - `test_faults` (injetor);
    - `test_infra` (perda, atraso, duplicação, partição).
  - No compose: `./scripts/run_scenario.sh scenarios/<cenário>.yaml` roda o cenário, coleta os logs em `results/raw/<cenário>_seed<SEED>/` e roda o verificador sobre eles (`verifications.txt` na mesma pasta).

## 2. Cenário normal e de falha de cada algoritmo

| | Cenário normal | Cenário de falha |
|---|---|---|
| Detector | `test_rede_estavel_sem_falso_positivo` | `test_crash_detectado_por_todos_e_recuperacao` |
| Bully | `test_cenario_normal_lider_e_o_maior_id` | `test_lider_cai_novo_lider_e_anunciado_e_maior_id_volta` |
| Os dois, com o verificador | `test_sanity` | `test_execucao_real_com_crash_e_retorno_sem_avisos` |

## 3. Verificador de logs

- Uso, da raiz do repositório:
  ```bash
  python -m analysis.log_verifier                     # results/logs -> results/verifications.txt
  python -m analysis.log_verifier results/raw/particao_rede_seed1001 --saida /tmp/v.txt
  ```
- O verificador lê todos os `node*.jsonl` da pasta e escreve:
  - um cabeçalho com nós, vidas, eventos, duração e falhas de rede encontradas no log;
  - uma linha `[OK]` ou `[AVISO]` por propriedade. O `[AVISO]` mostra até 3 exemplos, com tempos contados do primeiro `node_start`.
- **Vidas:** cada arquivo é dividido em encarnações do nó, de um `node_start` até o `node_stop`. Quando não há `node_stop` (SIGKILL), a vida vai até o último evento registrado. O nó está vivo dentro de uma das suas vidas.
- Propriedades:

| Propriedade | Tipo | O que confere |
|---|---|---|
| `detector.suspeita_apos_timeout` | segurança | Todo `suspect` vem depois de mais de `timeout` sem `recv` de heartbeat daquele peer, contando desde o `detector_start`. Usa os `recv` do log, não o valor anotado pelo próprio detector. |
| `detector.alternancia` | segurança | `suspect` e `restore` de um mesmo peer se alternam, começando por `suspect`. |
| `detector.completude` | vivacidade | Todo silêncio de um peer maior que `timeout + intervalo + folga` tem um `suspect` dentro desse prazo depois do último heartbeat. Cobre crash e partição. |
| `detector.restauracao` | vivacidade (precisão eventual) | Depois de um `suspect`, o `restore` vem em até `intervalo + folga` depois do primeiro heartbeat seguinte. |
| `bully.acordo` | segurança | Todo `leader` decide um líder L maior ou igual a todo nó que ficou vivo durante toda a eleição de L, do último `election_start` de L até a decisão. Um nó que sobe ou cai no meio da eleição fica de fora, como no modelo. |
| `bully.convergencia` | vivacidade | Depois que um nó sobe ou cai (início ou fim de uma vida), ou que uma falha de rede termina (`partition_end`, `faults_reset`), todos os vivos têm o maior vivo como líder ao fim do prazo `HB_TIMEOUT + HB_INTERVAL + 2 × RPC_TIMEOUT + folga`. Se antes do prazo outro nó sobe ou cai, ou uma falha de rede começa (`partition_start`, `faults_updated`), a cobrança é conferida um instante antes disso e só é descartada se ainda não tinha convergido (contada como "não conferida"). Durante uma falha de rede não há cobrança. O `[OK]` mostra o maior tempo até convergir. |

- Os parâmetros vêm do próprio log: `intervalo`, `timeout` e `peers` vêm do `detector_start`, e o `RPC_TIMEOUT` vem do `bully_start`.
- Tolerâncias:
  - `folga` (0,5 s, opção `--folga`): atraso de escalonamento das threads admitido nos prazos de vivacidade.
  - margem de 0,05 s em `detector.suspeita_apos_timeout`: o `recv` é logado um instante antes de o detector anotar o heartbeat.
- Quando o `[AVISO]` é esperado: as propriedades valem no período síncrono (modelos §2).
  - Com partição, cada lado elege o seu líder (`bully.acordo`) e não há convergência enquanto ela durar.
  - Com perda, a convergência acontece, mas sem limite de tempo; e um `ELECTION` ou OK perdido pode deixar um nó menor vencer com um maior vivo (`bully.acordo`).
  - Medidas com processos locais (sem Docker):
    - 5 nós, SIGKILL do líder e retorno, sem perda: as 6 propriedades deram `[OK]`.
    - O mesmo com 20% de perda: `[AVISO] bully.convergencia` (o líder só ficou certo em todos 5,36 s depois do crash, com prazo de 5,10 s); as outras 5 deram `[OK]`.
    - `particao_rede.yaml` com 3 nós: `[AVISO] bully.acordo` (os nós 1 e 2 elegem o 2 com o 3 vivo do outro lado); as outras 5 deram `[OK]`, com no máximo 0,17 s para convergir depois de cada mudança.
  - O cenário `particao_rede.yaml` dura 15 s para que, depois da cura (8 s), sobre mais que o prazo da convergência (5 s com os padrões). Um cenário mais curto que isso deixa a cobrança da cura "não conferida".
- **Teste do verificador:** `test_violacao_deliberada_e_detectada` parte de um log sintético correto e quebra uma propriedade por vez. O verificador precisa acusar exatamente as propriedades quebradas:

| Violação deliberada | Propriedade acusada |
|---|---|
| suspeita com heartbeats chegando | `detector.suspeita_apos_timeout` |
| `restore` sem `suspect` antes | `detector.alternancia` |
| peer em silêncio e nunca suspeito | `detector.completude` |
| suspeita nunca retirada, com heartbeats de volta | `detector.restauracao` |
| nó decide líder 2 com o nó 3 vivo | `bully.acordo` |
| suspeita só 1,5 s depois do último heartbeat (prazo de 1,1 s) | `detector.completude` |
| líder cai e os outros ficam com ele, com um `faults_reset` no fim, como faz o injetor | `bully.convergencia` |
| sem mudança nenhuma, o nó 2 se declara líder e fica assim | `bully.acordo` e `bully.convergencia` |

  Na outra direção, nenhuma destas execuções corretas pode gerar `[AVISO]`:
  - `test_execucao_correta_sem_avisos`, com logs sintéticos:
    - sem falhas;
    - crash por SIGKILL, com a última linha cortada;
    - crash por `stop()`, com eventos depois do `node_stop`;
    - o nó 3 volta no meio da eleição do nó 2;
    - logs sem `peers` no `detector_start` (gerados antes desta issue).
  - `test_execucao_real_com_crash_e_retorno_sem_avisos`, com nós reais.

## 4. Como rodar

- `python -m pytest`: todas as camadas que não precisam de Docker (cerca de 40 s).
- CI: `.github/workflows/ci.yml` roda o pytest com Python 3.12 a cada push e pull request. O CI não roda Docker; a camada do compose é rodada à mão.

## 5. Limitações

- O verificador compara instantes de logs de nós diferentes. Isso vale porque todos os nós rodam na mesma máquina, com o mesmo relógio; em várias máquinas seria preciso sincronizar os relógios.
- Depois de um SIGKILL, a vida termina no último evento registrado, um pouco antes do crash real (até cerca de um `HB_INTERVAL`). Os tempos de convergência medidos a partir daí ficam um pouco maiores, o que deixa essa verificação mais rigorosa. Já em `bully.acordo` o nó é dado como morto um pouco mais cedo, o que deixa essa verificação um pouco mais leniente nessa janela.
- O cabeçalho só vê as falhas de rede que aparecem como eventos (`drop`, `partition_start`, `faults_updated`, …). Um atraso configurado na subida (`NET_DELAY_MS`) não gera evento.
- As propriedades são conferidas na execução registrada. Um `[OK]` mostra que aquela execução respeitou as propriedades; não prova que todas respeitam.
