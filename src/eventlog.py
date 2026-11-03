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
        self.arquivo = open(self.path, "a", encoding="utf-8")

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


def read_events(path):
    # lê um log JSONL e devolve a lista de eventos
    eventos = []
    with open(path, encoding="utf-8") as f:
        for linha in f:
            if linha.strip() != "":
                eventos.append(json.loads(linha))
    return eventos
