# Camada de rede: envia mensagens HTTP/JSON para os outros nós.
#
# Pontos importantes (vão para o modelo do sistema no relatório):
#  - toda chamada tem timeout; sem timeout o nó poderia travar esperando um nó que caiu
#  - send() devolve None se a mensagem se perdeu ou se o outro nó não respondeu;
#    quem chama decide se tenta de novo
#  - perda, atraso e duplicação são simulados aqui no emissor, usando um random com
#    semente fixa, então dá para repetir o mesmo experimento
#  - block(peer) simula uma partição de rede
import json
import random
import threading
import time
import urllib.request

from .config import NetFaults
from .models import Message


class Network:
    def __init__(self, node_id, peers, clock, log, default_timeout=1.0, faults=None, seed=0):
        if default_timeout <= 0:
            raise ValueError("timeout deve ser maior que 0")
        self.node_id = node_id
        self.peers = dict(peers)
        self.clock = clock
        self.log = log
        self.default_timeout = default_timeout
        self.faults = faults if faults is not None else NetFaults()
        self.rng = random.Random(seed)
        self.bloqueados = set()      # peers "isolados" (partição simulada)
        self.lock = threading.Lock()

    # --- controle dinâmico de falhas (usado pelo injetor de falhas) ---
    def set_faults(self, faults):
        with self.lock:
            self.faults = faults
        self.log.emit(
            "faults_updated",
            drop_prob=faults.drop_prob,
            delay_ms=faults.delay_ms,
            jitter_ms=faults.jitter_ms,
            dup_prob=faults.dup_prob,
        )

    def reset_faults(self):
        with self.lock:
            self.faults = NetFaults()
            self.bloqueados.clear()
        self.log.emit("faults_reset")

    def block(self, peer_id):
        with self.lock:
            self.bloqueados.add(peer_id)
        self.log.emit("partition_start", peer=peer_id)

    def unblock(self, peer_id):
        with self.lock:
            self.bloqueados.discard(peer_id)
        self.log.emit("partition_end", peer=peer_id)

    def get_status(self):
        with self.lock:
            return {
                "bloqueados": sorted(list(self.bloqueados)),
                "drop_prob": self.faults.drop_prob,
                "delay_ms": self.faults.delay_ms,
                "jitter_ms": self.faults.jitter_ms,
                "dup_prob": self.faults.dup_prob,
            }

    def sorteio(self):
        # número aleatório entre 0 e 1 (com lock porque várias threads enviam)
        with self.lock:
            return self.rng.random()

    # --- envio ---
    def send(self, peer_id, msg_type, payload=None, timeout=None):
        if timeout is None:
            timeout = self.default_timeout
        if timeout <= 0:
            raise ValueError("timeout deve ser maior que 0")

        msg = Message(msg_type, self.node_id, self.clock.tick(), payload or {})

        # partição: não envia nada se o peer estiver na lista de bloqueados
        with self.lock:
            isolado = peer_id in self.bloqueados
            faults = self.faults

        # Numa rede real o emissor não fica sabendo que a mensagem se perdeu: ele só
        # desiste quando o timeout expira. Por isso esperamos o timeout antes de
        # devolver None, senão os tempos medidos sob falha sairiam otimistas.
        if isolado:
            self.log.emit("drop", peer=peer_id, msg=msg_type, reason="partition")
            time.sleep(timeout)
            return None

        # perda da requisição simulada (o receptor nunca recebe a mensagem)
        if faults.drop_prob > 0 and self.sorteio() < faults.drop_prob:
            self.log.emit("drop", peer=peer_id, msg=msg_type, reason="loss")
            time.sleep(timeout)
            return None

        # atraso simulado
        atraso = faults.delay_ms
        if faults.jitter_ms > 0:
            atraso = atraso + self.sorteio() * faults.jitter_ms
        if atraso > 0:
            time.sleep(atraso / 1000)

        # duplicação: manda a mesma mensagem duas vezes
        vezes = 1
        if faults.dup_prob > 0 and self.sorteio() < faults.dup_prob:
            vezes = 2

        resposta = None
        for i in range(vezes):
            self.log.emit("send", peer=peer_id, msg=msg_type, dup=(i == 1))
            resposta = self.post(peer_id, msg_type, msg, timeout)
        return resposta

    def post(self, peer_id, msg_type, msg, timeout):
        url = "http://" + self.peers[peer_id] + "/msg"
        dados = json.dumps(msg.to_dict()).encode("utf-8")
        req = urllib.request.Request(url, data=dados, method="POST",
                                     headers={"Content-Type": "application/json"})
        inicio = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                corpo = json.loads(r.read().decode("utf-8"))
        except Exception as erro:
            # timeout, conexão recusada, nó fora do ar, resposta inválida...
            rtt = round((time.monotonic() - inicio) * 1000, 3)
            self.log.emit("send_failed", peer=peer_id, msg=msg_type,
                          error=type(erro).__name__, rtt_ms=rtt)
            return None

        # perda da resposta simulada: sorteia de novo com a mesma drop_prob (cada
        # sentido perde de forma independente). O receptor JÁ processou a mensagem;
        # só a resposta não chega, e o emissor espera o resto do timeout.
        with self.lock:
            drop_prob = self.faults.drop_prob
        if drop_prob > 0 and self.sorteio() < drop_prob:
            self.log.emit("drop", peer=peer_id, msg=msg_type, reason="reply_loss")
            decorrido = time.monotonic() - inicio
            time.sleep(max(0, timeout - decorrido))
            return None

        resposta = Message.from_dict(corpo)
        self.clock.update(resposta.clock)
        rtt = round((time.monotonic() - inicio) * 1000, 3)
        self.log.emit("reply", peer=peer_id, msg=msg_type, rtt_ms=rtt)
        return resposta
