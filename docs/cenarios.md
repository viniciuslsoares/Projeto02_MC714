# Cenários de falha: recuperação, partição e falhas combinadas

Implementação: `scenarios/*.yaml` e `experiments/cenarios.py` (issue #7), com a extensão do verificador em `analysis/log_verifier.py`. Os cenários usam o framework da issue #2 (YAML + `FaultInjector`) e os processos locais dos experimentos da #6.

## 1. Como rodar

```bash
python -m experiments.cenarios                                      # todos os cenários, 5 repetições (~10 min)
python -m experiments.cenarios scenarios/particao_menor_id.yaml --repeticoes 1 --saida /tmp/teste
python -m experiments.run_all --grades cenarios                     # o mesmo que o primeiro, pela porta de entrada única
```

- Sem `--saida`, o CSV versionado é sobrescrito: use outra pasta para rodadas parciais.
- Ctrl+C ou SIGTERM param os nós e gravam as execuções que já terminaram (`completa: false` no `.meta.json`).

- Precisa do `pyyaml` (`requirements.txt`) para ler os cenários; o resto é a biblioteca padrão.
- Saída, versionada no git (dados brutos):
  - `results/experimentos/cenarios.csv`, com uma linha por execução;
  - `results/experimentos/cenarios.csv.meta.json`, com a mesma proveniência das grades (`docs/experimentos.md` §6), a definição de cada cenário e a descrição das colunas.
- Os logs de cada execução ficam em `results/raw/cenarios/<cenário>_seed<semente>/`, fora do git, e podem ser conferidos com `python -m analysis.log_verifier <pasta>`.
- No compose: `./scripts/run_scenario.sh scenarios/<cenário>.yaml`. Os cenários usam os nós 1 a 3, os do compose.

## 2. Execução

1. Sobe 3 nós, cada um num processo próprio (`python -m src.node`) em 127.0.0.1, com os padrões do compose: `HB_INTERVAL` = 0,5 s, `HB_TIMEOUT` = 2,0 s e `RPC_TIMEOUT` = 1,0 s.
2. Espera todos terem o nó 3 como líder e mais 2 s de aquecimento.
3. O `FaultInjector` executa os eventos do YAML nos instantes marcados:
   - `partition`, `heal`, `set_faults` e `reset_faults`: pelo `/control` de cada nó;
   - `crash`: SIGKILL do processo, sem `node_stop` no log;
   - `recover`: um processo novo, na mesma porta e com o mesmo arquivo de log. O nó volta sem estado (o Bully não tem persistência, `docs/modelo_bully.md` §2) e sem as falhas de rede que tinha, que ficam só na memória.
4. No fim, o injetor faz `reset_faults` em todos os nós; os nós são parados com SIGTERM.

- Sementes: a repetição r usa `SEED` = `seed` do YAML + r − 1. A semente sorteia as perdas (cenário combinado) e o jitter (`queda_e_recuperacao`). Fora disso, a variação entre repetições vem do escalonamento e da fase dos eventos em relação aos heartbeats.
- As repetições ficam por fora, como nas grades da #6.

## 3. Cenários

| Cenário | Tipo | Eventos (s) | O que mostra |
|---|---|---|---|
| `cascata_e_recuperacao` (novo) | recuperação e cascata | 3: crash do 3 (líder); 4: crash do 2; 10: volta do 3; 16: volta do 2 | O próximo líder cai antes de os outros perceberem a primeira queda, e sobra o nó 1. O líder antigo volta e retoma a liderança; o nó 2 volta e só reconhece o 3. |
| `particao_menor_id` (novo) | partição | 3: isola o nó 1 de 2 e 3; 9: cura | O nó 1, sozinho, suspeita dos outros e se elege: dois líderes (1 e 3) até a cura. |
| `particao_queda_e_perda` (novo) | combinado | 3: isola o 3 de 1 e 2; 8: perda de 20% em todos; 10: crash do 2 (líder do lado 1–2); 16: `reset_faults` em todos (cura e fim da perda); 21: volta do 2 | Partição, perda e queda de líder durante a partição. Primeiro os líderes são 2 e 3; depois da queda do 2, são 1 e 3. |
| `particao_rede` (#2) | partição | 3: isola o 3 (líder) de 1 e 2; 8: cura | O lado sem o líder elege o 2: dois líderes (2 e 3) até a cura. |
| `queda_e_recuperacao` (#2) | recuperação | 3: crash do 2; 5: atraso de 100 ms no 1; 8: volta do 2; 11: fim do atraso | Um nó que não é o líder cai e volta. |

- Nos cenários novos, depois da segunda queda da cascata, de cada cura e de cada volta sobra mais que o prazo de convergência do verificador (5 s com os padrões). A perda do cenário combinado começa depois de o lado 1–2 eleger o 2, para que a queda seja mesmo a do líder desse lado.
- Na cura de `particao_queda_e_perda`, o comando para o nó 2 (caído) falha, e o injetor mostra `[Aviso] Falha ao enviar comando para nó 2`. É esperado.

## 4. Métricas (colunas do CSV)

| Coluna | Definição, a partir dos logs |
|---|---|
| `eventos` | Os eventos do cenário, na ordem, como `ação:nó` (na partição e na cura, o nó isolado). |
| `reconvergencia_s` | Para cada evento: tempo do evento até o início do último trecho, antes do próximo evento, em que todos os nós vivos têm o maior vivo como líder. Separados por `;`, na ordem de `eventos`. Vazio se não tinha convergido quando veio o próximo evento (ou o fim). |
| `lideres_max` | O maior número de nós vivos que se acharam líderes ao mesmo tempo (o último `leader` do nó é ele mesmo), durante o cenário. |
| `dois_lideres_s` | Tempo, durante o cenário, com mais de um nó se achando líder. |
| `avisos_verificador` | Propriedades com `[AVISO]` no verificador, separadas por `;` (§6). |

- **Tempo de recuperação:** é a `reconvergencia_s` de um `recover`, do comando de reinício até todos os vivos, inclusive o nó que voltou, terem o maior vivo como líder. Inclui a subida do processo Python (cerca de 0,1 a 0,2 s aqui). É o tempo de reparo do serviço de eleição, o MTTR da Aula 3.
- **Tempo de reconvergência depois da partição:** é a `reconvergencia_s` do `heal`.
- Depois de um `partition`, a `reconvergencia_s` fica vazia: até a cura, cada lado tem o seu líder.
- Depois de um evento que não muda o líder (crash de quem não é líder, atraso), ela costuma ser 0: o sistema já estava convergido e continuou assim. Durante uma partição, qualquer evento fica vazio.
- O crash conta do SIGKILL. Sem `node_stop`, a vida do nó no log terminaria no último evento registrado, um pouco antes; nas medidas daqui, ela vai até o instante do crash.
- Outras colunas registram a configuração: `cenario`, `repeticao`, `seed`, `n`, `hb_intervalo`, `hb_timeout`, `rpc_timeout` e `duracao_s`.

## 5. Resultados

RESULTADOS

## 6. O que se mantém e o que é violado

As propriedades e os prazos são os de `docs/modelo_detector.md` §3 e `docs/modelo_bully.md` §3, conferidos pelo verificador (`docs/testes.md` §3).

| Propriedade | Cascata e recuperação | Partição | Combinado | Por quê |
|---|---|---|---|---|
| `detector.suspeita_apos_timeout` (segurança) | mantém | mantém | mantém | É local: só depende do relógio do nó e dos heartbeats que chegaram. A falha só atrasa ou impede heartbeats. |
| `detector.alternancia` (segurança) | mantém | mantém | mantém | Suspeitas e restaurações saem de uma thread só. |
| `detector.completude` (vivacidade) | mantém | mantém | mantém | Partição e crash são o mesmo silêncio: os dois lados se suspeitam. O detector não distingue um nó caído de um nó inalcançável (Aula 3: não dá para distinguir falha de lentidão). |
| `detector.restauracao` (vivacidade) | mantém | mantém | mantém | Depois da cura, ou da volta do nó, os heartbeats voltam e a suspeita é retirada. |
| `bully.acordo` (segurança) | mantém | **violada** | **violada** | O lado sem o maior ID não recebe resposta dele e o trata como caído. O Bully supõe um sistema síncrono com canais confiáveis (`docs/modelo_bully.md` §2): quem não responde dentro do timeout caiu. Na partição, o nó maior está vivo, mas inalcançável. Também não há quórum: qualquer grupo, até um nó sozinho, elege o seu líder (split-brain). Com perda, um `ELECTION` ou OK perdido pode ter o mesmo efeito. |
| `bully.convergencia` (vivacidade) | mantém | mantém depois da cura | mantém depois da cura | Enquanto a partição dura, há dois líderes e não há convergência, e a queda do nó 2 no cenário combinado não é cobrada (§7). Depois da cura, o lado menor vê vivo um nó maior e faz uma eleição, que o maior vence. Com perda, converge, mas sem limite de tempo: o Bully não retransmite o `COORDINATOR`. |

- Na cascata, a segunda queda chega antes de a primeira ser percebida. A convergência medida depois do segundo crash já inclui a primeira detecção.
- Nos cenários da #2: `particao_rede` viola o acordo pelo mesmo motivo; `queda_e_recuperacao` mantém tudo.

## 7. Extensão do verificador

- **Sem cobrança de convergência com partição ou perda ativas.** Uma mudança (nó sobe ou cai) que acontece durante uma partição ou perda ligadas pelo `/control` não abre cobrança: sem resposta, não há período síncrono nem prazo. A cura (`partition_end`, `faults_reset`) abre outra cobrança, conferida normalmente. Atraso e duplicação continuam cobrados, como antes: as chamadas continuam sendo respondidas.
  - Antes, um nó que caía durante a partição dava `[AVISO] bully.convergencia` ("não tinha convergido 5,00 s depois de fim do nó 2"). A propriedade não promete convergência durante a partição.
  - A perda configurada na subida (`NET_DROP_PROB`, como nas grades da #6) não gera evento, então continua cobrada, como antes.
- **Líderes ao mesmo tempo.** O cabeçalho do relatório diz quantos nós se acharam líderes ao mesmo tempo e por quanto tempo houve mais de um. Não é uma propriedade com `[OK]`/`[AVISO]`: o Bully não garante um líder só, nem sem falhas, porque há dois por um instante na troca de líder (`docs/modelo_bully.md` §5). É a medida do split-brain.
- `experiments/cenarios.py` usa as mesmas funções do verificador (`inicio_convergido`, `lideres_ao_mesmo_tempo`).

## 8. Limitações (hipóteses violadas)

- **Partição viola o modelo do Bully.** Ela quebra a hipótese de sistema síncrono com canais confiáveis, e o resultado é split-brain. Evitar isso pediria quórum de maioria (só lidera quem alcança a maioria) ou épocas persistidas, como no Raft. Isso fica fora do escopo: o objetivo aqui é medir a consequência, não corrigir o algoritmo.
- **Recuperação sem persistência.** O nó volta sem estado e faz uma eleição. Um líder antigo que volta retoma a liderança, porque o Bully sempre elege o maior vivo: cada volta do maior ID troca o líder de novo, com dois líderes por um instante.
- **Só partições simétricas.** O injetor bloqueia os dois sentidos. Uma partição assimétrica (um sentido só) pode causar eleições repetidas (`docs/modelo_bully.md` §5) e não foi testada.
- **Partição no nível da aplicação.** A mensagem é descartada no emissor, que espera o timeout, como numa rede que não responde. Um crash, por outro lado, recusa a conexão na hora (`docs/modelo_falhas.md` §6).
- **Um nó que volta começa sem as falhas de rede que tinha** (elas ficam em memória). No cenário combinado, a volta é depois do `reset_faults`, então isso não aparece.
- **Processos numa só máquina**, como nos experimentos da #6 (`docs/experimentos.md` §7). No compose, o crash e a volta passam pelo Docker e demoram mais.
- **5 repetições por cenário.** Bastam para mostrar o comportamento e a variação, mas não para percentis altos. A semente só muda os sorteios de perda e de jitter.
- **Sem cobrança durante partição ou perda.** O verificador não diz nada sobre a convergência enquanto elas estão ligadas; a `reconvergencia_s` mede esses casos.

## Referências

- M. van Steen e A. S. Tanenbaum, *Distributed Systems*, 4ª ed.: cap. 5 (eleição de líder, algoritmo do valentão) e cap. 8 (tolerância a falhas: modelos de falha, detecção de falhas, partições e recuperação).
- Aula 3 (MC714): *dependability*, falhas parciais, mascarar e recuperar falhas, MTTF/MTTR, e a dificuldade de distinguir falha de lentidão.
