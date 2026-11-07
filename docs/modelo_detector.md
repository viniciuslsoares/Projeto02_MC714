# Detector de falhas por heartbeat — modelo do sistema

Implementação: `src/detector.py` (issue #3). Testes: `tests/test_detector.py`.

## 1. Algoritmo

- A cada `intervalo` (`HB_INTERVAL`), cada nó envia uma mensagem `heartbeat` a cada peer, com uma thread por peer: um peer isolado ou lento não atrasa os heartbeats para os outros. O heartbeat não precisa de resposta, por isso é enviado com timeout igual ao `intervalo`.
- Ao receber um heartbeat, o nó anota o instante de chegada (`time.monotonic()`).
- A cada `intervalo`, uma thread de verificação confere cada peer: se está há mais de `timeout` (`HB_TIMEOUT`) sem mandar heartbeat, ele passa a ser **suspeito**; se era suspeito e voltou a mandar, é **restaurado**. Suspeitas e restaurações só acontecem nessa thread, então os eventos de um mesmo peer saem sempre em ordem.
- No início, cada peer tem um prazo de `timeout` para mandar o primeiro heartbeat; um peer que nunca sobe também é suspeito.
- API: `on_suspect(f)`, `on_restore(f)` (callbacks `f(peer_id)`, usados pelo Bully #4 e pela integração #8) e `vivos()`.
- Log: `detector_start`, `suspect` (com `sem_heartbeat_s`), `restore`, `callback_error`. As mensagens aparecem como `send`/`recv`/`reply` com `msg="heartbeat"`.

## 2. Modelo do sistema

- **Temporal: parcialmente síncrono.** Num sistema assíncrono não dá para distinguir um nó lento de um nó caído, e nenhum timeout é seguro. Supomos que, a partir de um instante desconhecido, o atraso dos heartbeats fica abaixo de um limite Δ, com `intervalo + Δ < timeout`. Antes disso, pode haver falsas suspeitas.
- **Canais:** os de `docs/modelo_falhas.md` §6 (sem FIFO, melhor esforço, perda, duplicação). O heartbeat depende só da requisição: perda da resposta não importa. Com `drop_prob = p` no emissor, cada heartbeat se perde com probabilidade p. Duplicação e reordenação são inofensivas, porque receber um heartbeat só atualiza o instante do último.
- **Falhas:** crash-stop (o nó para de mandar heartbeats e é suspeito); crash-recovery (o nó volta sem estado, dá a todos os peers um novo prazo de `timeout` e é restaurado pelos outros no primeiro heartbeat); partição (os dois lados se suspeitam e se restauram depois da cura; o detector não distingue partição de crash). Não consideramos falhas bizantinas.
- **Relógios:** só o relógio monotônico local, para medir intervalos; não há sincronização entre nós. O relógio de Lamport só ordena os eventos nos logs.
- **Persistência:** nenhuma; todo o estado fica em memória.

## 3. Propriedades

- **Completude (vivacidade):** todo nó que cai passa a ser suspeito por todos os nós corretos. Limite: o último heartbeat recebido é anterior ao crash, e a verificação roda a cada `intervalo`; logo, a detecção acontece até `timeout + intervalo` depois do crash (2,5 s com os padrões 0,5 s / 2,0 s).
- **Precisão (segurança, garantida só eventualmente):** no modelo parcialmente síncrono, depois que os atrasos se estabilizam abaixo de Δ, nenhum nó correto é suspeito. Com perda probabilística isso não é garantido: um falso positivo exige perder cerca de k = `timeout / intervalo` heartbeats seguidos, com probabilidade ≈ pᵏ por janela (k = 4 nos padrões). Os falsos positivos são medidos nos experimentos (#6), e o peer é restaurado sozinho quando o próximo heartbeat chega.
- **Custo:** n(n − 1) heartbeats por `intervalo` no sistema, O(n²). Para medir mensagens/s, filtrar `msg="heartbeat"` (o `PING_INTERVAL` da infraestrutura gera tráfego à parte).

## 4. Verificação

- `test_suspeita_e_restauracao` (unitário): suspeita após `timeout`, não repete o aviso, restaura no heartbeat, sobrevive a callback com erro.
- `test_rede_estavel_sem_falso_positivo`: 3 nós, 20 intervalos, nenhuma suspeita.
- `test_crash_detectado_por_todos_e_recuperacao`: o nó 3 cai e é suspeito pelos nós 1 e 2 dentro de `timeout + intervalo` (com folga); ao voltar, é restaurado pelos dois.

## 5. Limitações

- Timeout fixo; o timeout adaptativo (opcional na issue) não foi implementado.
- A detecção usa só a ausência de heartbeats: mesmo quando a conexão é recusada na hora (nó caído), o detector espera o `timeout`.
