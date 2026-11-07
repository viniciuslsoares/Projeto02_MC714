# Eleição de líder (Bully) — modelo do sistema

Implementação: `src/bully.py` (issue #4). Testes: `tests/test_bully.py`. Usa o detector de falhas da issue #3 (`docs/modelo_detector.md`).

## 1. Algoritmo

- IDs únicos e fixos (`NODE_ID`); o líder é o maior ID correto.
- Mensagens:
  - `ELECTION`: vai aos nós de ID maior.
  - `OK`: é a resposta HTTP ao `ELECTION` (`{"msg": "OK"}`).
  - `COORDINATOR`: o vencedor manda a **todos** os nós.
- Eleição no nó i:
  1. i manda `ELECTION` a todos os maiores, em paralelo, e espera até `RPC_TIMEOUT`.
  2. Se nenhum OK chega, i vence: líder = i, e manda `COORDINATOR` a todos.
  3. Se chega um OK, i espera o `COORDINATOR` até `2 × RPC_TIMEOUT`. Se ele não chega, i repete o passo 1.
- Quem recebe `ELECTION` responde OK e faz a própria eleição.
- Quem recebe `COORDINATOR` de um nó maior aceita o novo líder.
- Quem recebe `COORDINATOR` de um nó menor rejeita e faz a própria eleição (o "valentão").
- Quando começa uma eleição: a thread `bully` confere a cada `HB_INTERVAL`, e também quando o detector avisa uma suspeita ou restauração. Há eleição se:
  - o nó não tem líder (acabou de subir);
  - o detector suspeita do líder;
  - um nó maior que o líder está vivo para o detector;
  - chegou `ELECTION` ou `COORDINATOR` de um nó menor.
- Eleições concorrentes:
  - Cada nó faz no máximo uma eleição por vez.
  - Um `ELECTION` recebido durante a eleição não abre outra: ela termina com um `COORDINATOR` para todos. Se quem pediu não recebe esse `COORDINATOR` (por exemplo, porque já o tinha recebido antes de começar a própria eleição), repete a eleição depois de `2 × RPC_TIMEOUT` (`sem_coordinator`). Isso custa tempo e mensagens, mas não muda o líder.
  - Se um nó maior se anuncia enquanto o nó espera os OKs, o nó não se declara líder.
- Contador de mensagens:
  - `election_end` traz em `msgs` quantas `ELECTION`, `OK` e `COORDINATOR` o nó enviou naquela eleição.
  - `ELECTION` e `COORDINATOR` contam as chamadas a `send()`, inclusive para nós caídos e as perdidas. `OK` conta os `ELECTION` atendidos.
  - Com duplicação (`NET_DUP_PROB`), uma chamada vira dois `send` no log da rede e gera dois OKs. Por isso, sob falhas, `msgs` não bate com os eventos `send`.
  - O custo de uma eleição é a soma dos `election_end` de todos os nós.
- Log:
  - `bully_start` (`timeout`), quando o Bully começa a rodar no nó;
  - `election_start` (`motivo`, `maiores`);
  - `leader` (`lider`), quando o nó decide um líder;
  - `election_end` (`lider`, `msgs`);
  - `coordinator_rejected` (`peer`).
  - As mensagens aparecem como `send`/`recv`/`reply` com `msg="ELECTION"`/`"COORDINATOR"`.
  - `GET /status` mostra o `lider` atual.

## 2. Modelo do sistema

- **Temporal: parcialmente síncrono**, como o detector.
  - O Bully supõe um sistema síncrono: um nó maior que não responde dentro do timeout é tratado como caído. Num sistema assíncrono, isso pode eleger um nó menor com um maior ainda vivo.
  - Por isso, a segurança vale nos períodos síncronos: toda chamada a um nó correto é respondida em menos de `RPC_TIMEOUT`.
  - Fora desses períodos (perda, partição, atraso), pode haver líderes errados por um tempo, corrigidos quando a rede se normaliza (§3).
- **Canais:** os de `docs/modelo_falhas.md` §6, com estas consequências:
  - Sem FIFO. O algoritmo não depende de ordem: cada mensagem é tratada pelo ID de quem a mandou.
  - Perda: o Bully não retransmite. Uma eleição sem resposta conta como "nenhum OK", e a conferência periódica refaz a eleição enquanto a situação estiver errada.
  - Duplicação: `ELECTION` duplicado gera dois OKs e não abre outra eleição; `COORDINATOR` duplicado repete o mesmo líder. Os handlers são idempotentes.
- **Falhas:**
  - **Crash-stop:** o detector suspeita do líder caído, e os outros elegem o maior restante.
  - **Crash-recovery:** o nó volta sem estado, sem líder, e faz uma eleição.
  - **Reentrada de nó com ID maior:** ele não acha ninguém maior, vence e anuncia a todos, que o aceitam. Os outros também o veem vivo de novo pelo detector e chamam uma eleição, que ele vence.
  - **Partição:** cada lado elege o seu líder. Depois da cura, o lado menor vê vivo um nó maior e faz uma eleição, que o maior ID vence.
  - Sem falhas bizantinas.
- **Relógios:** só o relógio monotônico local, para os timeouts (dentro de `send()` e das esperas). O relógio de Lamport só ordena os logs.
- **Persistência:** nenhuma. O líder é determinado pelos IDs dos nós vivos, e o nó que volta descobre o líder fazendo uma eleição. Ver `docs/modelo_falhas.md` §7.

## 3. Propriedades

- **Segurança (acordo):** no período síncrono, se nenhum nó cai ou volta durante a eleição, todo nó que decide um líder (evento `leader`) decide o maior ID correto. Logo, todos os nós corretos concordam.
  - O nó que vence não recebeu OK de nenhum maior; como todo nó correto responde a tempo, não há maior correto.
  - Um nó só aceita `COORDINATOR` de um nó maior que ele.
- **Por que não há "época":** sem persistência, um contador de época voltaria a zero justamente no nó que volta e vence (o de maior ID). Por isso a segurança é dita como acordo sobre o maior ID, sem número de época.
- **Vivacidade:** depois que param os crashes e retornos e a rede volta a ser síncrona, todos os nós corretos acabam com o maior ID correto como líder e param de fazer eleições.
  - Toda condição de eleição é conferida a cada `HB_INTERVAL` enquanto valer.
  - O maior correto vence qualquer eleição que o alcance.
  - Pela completude e pela precisão eventual do detector, ninguém fica com um líder caído nem suspeita para sempre do líder correto.
  - **Limite após a queda do líder:** `HB_TIMEOUT + HB_INTERVAL` para a detecção, mais `RPC_TIMEOUT` para os `ELECTION` aos maiores caídos, mais `RPC_TIMEOUT` para o `COORDINATOR`. Com os padrões, isso dá 2,0 + 0,5 + 1,0 + 1,0 = 4,5 s.
  - Com crash de processo a conexão é recusada na hora, e sobra praticamente só a detecção.
  - Sob perda aleatória, a convergência acontece com probabilidade 1 (cada nova tentativa tem chance de passar), mas sem limite de tempo.
- **Complexidade (queda do líder n, com n nós):**
  - **Melhor caso:** só o nó n − 1 começa. São 1 `ELECTION` e n − 1 `COORDINATOR`, O(n).
  - **Pior caso de uma rodada:** o nó 1 começa e cada nó i manda `ELECTION` aos n − i maiores. São n(n − 1)/2 `ELECTION`, (n − 1)(n − 2)/2 OKs e n − 1 `COORDINATOR`, O(n²).
  - **Rodadas extras:** essas fórmulas contam uma rodada. Os detectores não suspeitam do líder caído todos no mesmo instante: o primeiro nó que suspeita começa a eleição, e o novo líder vence logo.
    - O novo líder, e os nós que já o aceitaram, repetem a eleição (`no_maior_vivo`) enquanto o próprio detector ainda vê o líder antigo vivo. Isso dura até cerca de um `HB_INTERVAL`.
    - Cada repetição custa mais 1 `ELECTION` por nó maior caído e n − 1 `COORDINATOR`, além das cascatas de `ELECTION`/OK.
    - Por isso, o custo medido depois de um crash pode passar da fórmula de uma rodada. Na revisão, com n = 5, chegou a 14 `ELECTION`, 7 OKs e 12 `COORDINATOR`.
    - O líder eleito e o limite de tempo não mudam. Os experimentos (#6) medem o custo com o `msgs` de `election_end`.

## 4. Verificação

- `test_mensagens_recebidas` (unitário): OK ao `ELECTION`; aceita `COORDINATOR` de maior e rejeita o de menor.
- `test_quando_comeca_uma_eleicao` (unitário): sem líder, líder suspeito e nó maior vivo disparam eleição; líder vivo não dispara.
- `test_sem_ok_de_nenhum_maior_vira_lider` (unitário): sem OK, o nó vence e o contador sai certo.
- `test_host_padrao_e_loopback`, `test_parametros_invalidos` (unitários): `BIND_ADDR` padrão é 127.0.0.1; timeout inválido é rejeitado.
- `test_cenario_normal_lider_e_o_maior_id`: 4 nós começam juntos, e pelo menos duas eleições ficam abertas ao mesmo tempo (concorrentes). Todos ficam com o nó 4, e ninguém decide outro líder.
- `test_lider_cai_novo_lider_e_anunciado_e_maior_id_volta`:
  - o nó 4 cai, e os nós 1 a 3 elegem o 3 dentro do limite, sem decidir outro;
  - o nó 4 volta e todos passam a ter o 4;
  - os `msgs` de `election_end` batem com os `send`/`recv` do log.
- Verificador de logs (`analysis/log_verifier.py`, issue #5): confere em qualquer execução, inclusive no compose, o acordo (`bully.acordo`) e a convergência dentro do limite de tempo (`bully.convergencia`). Ver `docs/testes.md`.

## 5. Limitações

- **Troca de líder:** quando um nó maior volta, o líder antigo continua se achando líder até o `COORDINATOR` chegar (cerca de uma ida e volta). Não há "no máximo um líder a cada instante".
- **Subida em tempos diferentes:** no compose, os contêineres sobem com alguma diferença de tempo, e os primeiros podem se declarar líderes até o maior subir. É o mesmo caso da volta de um nó maior.
- **Partição:** há dois líderes, um por lado, até a cura. Partição assimétrica (um sentido só) pode causar eleições repetidas enquanto durar.
- **Timeouts fixos:** `RPC_TIMEOUT` serve para o OK e `2 × RPC_TIMEOUT` para o `COORDINATOR`; não há ajuste adaptativo.
