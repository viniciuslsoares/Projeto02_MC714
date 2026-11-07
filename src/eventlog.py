# Relógio de Lamport e log dos eventos de cada nó.
# O log é um arquivo JSONL (uma linha JSON por evento): results/logs/node<ID>.jsonl.
# Depois ele é lido pelos testes e pelo verificador de safety/liveness.
import json
import os
import threading
import time


class LamportClock:
    def __init__(self):
        self.valor = 0
        self.lock = threading.Lock()   # várias threads usam o relógio ao mesmo tempo

    def tick(self):
        # evento local ou envio: soma 1
        with self.lock:
            self.valor += 1
            return self.valor

    def update(self, recebido):
        # recebimento: pega o maior entre o meu e o da mensagem, e soma 1
        with self.lock:
            self.valor = max(self.valor, recebido) + 1
            return self.valor


class EventLog:
    def __init__(self, node_id, log_dir, clock, echo=True):
        self.node_id = node_id
        self.clock = clock
        self.echo = echo            # se True, também imprime (aparece no docker compose logs)
        self.lock = threading.Lock()
        os.makedirs(log_dir, exist_ok=True)
        self.path = os.path.join(log_dir, "node" + str(node_id) + ".jsonl")
        # Crash-recovery: o log de antes do reinício continua no disco. O relógio
        # retoma do maior valor já escrito, para nunca andar para trás (Aula 17),
        # e cada node_start anterior conta como uma "encarnação" do nó.
        maior, inicios, termina_no_meio = ler_log_anterior(self.path)
        clock.valor = max(clock.valor, maior)
        self.incarnation = inicios + 1
        self.arquivo = open(self.path, "a", encoding="utf-8")
        if termina_no_meio:
            # a última linha foi cortada (ex.: SIGKILL no meio da escrita);
            # quebra a linha para o próximo evento não ser grudado nela
            self.arquivo.write("\n")

    def emit(self, event, **data):
        linha = {
            "ts": time.time(),
            "node": self.node_id,
            "clock": self.clock.tick(),
            "event": event,
            "data": data,
        }
        texto = json.dumps(linha, sort_keys=True)
        with self.lock:
            self.arquivo.write(texto + "\n")
            self.arquivo.flush()
            if self.echo:
                print(texto, flush=True)

    def close(self):
        with self.lock:
            self.arquivo.close()


def ler_log_anterior(path):
    # Lê o log deixado por uma execução anterior do mesmo nó (se existir).
    # Devolve (maior clock, quantos node_start, se o arquivo termina sem "\n").
    # Linhas malformadas são ignoradas.
    maior = 0
    inicios = 0
    termina_no_meio = False
    if not os.path.exists(path):
        return maior, inicios, termina_no_meio
    with open(path, encoding="utf-8") as f:
        for linha in f:
            termina_no_meio = not linha.endswith("\n")
            try:
                evento = json.loads(linha)
            except ValueError:
                continue   # linha vazia ou truncada
            maior = max(maior, evento.get("clock", 0))
            if evento.get("event") == "node_start":
                inicios += 1
    return maior, inicios, termina_no_meio


def read_events(path):
    # lê um log JSONL e devolve a lista de eventos
    eventos = []
    with open(path, encoding="utf-8") as f:
        for linha in f:
            if linha.strip() != "":
                eventos.append(json.loads(linha))
    return eventos
