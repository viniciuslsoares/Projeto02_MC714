# Detector de falhas por heartbeat (issue #3).
#
# Cada nó manda uma mensagem "heartbeat" para cada peer a cada `intervalo`
# segundos e anota quando recebeu o último heartbeat de cada peer. A cada
# `intervalo`, confere:
#   - peer há mais de `timeout` segundos sem heartbeat -> fica suspeito (evento "suspect")
#   - peer suspeito que voltou a mandar heartbeat     -> suspeita retirada ("restore")
# As decisões só acontecem na thread de verificação, então "suspect" e
# "restore" de um mesmo peer nunca saem fora de ordem.
#
# API para os outros módulos (Bully, integração):
#   detector.on_suspect(f)   chama f(peer_id) quando peer_id passa a ser suspeito
#   detector.on_restore(f)   chama f(peer_id) quando peer_id deixa de ser suspeito
#   detector.vivos()         lista dos peers não suspeitos agora
# Os callbacks rodam na thread do detector: devem ser rápidos (se precisarem
# mandar mensagens, que criem uma thread com node.spawn).
#
# O modelo do sistema e as propriedades estão em docs/modelo_detector.md.
import threading
import time


class HeartbeatDetector:
    def __init__(self, node, intervalo=0.5, timeout=2.0):
        if intervalo <= 0 or timeout <= intervalo:
            raise ValueError("use 0 < intervalo < timeout")
        self.node = node
        self.intervalo = intervalo
        self.timeout = timeout
        self.ultimo = {}          # peer -> instante (time.monotonic) do último heartbeat recebido
        self.suspeitos = set()
        self.lock = threading.Lock()
        self.callbacks_suspect = []
        self.callbacks_restore = []
        node.register("heartbeat", self.on_heartbeat)

    def on_suspect(self, funcao):
        self.callbacks_suspect.append(funcao)

    def on_restore(self, funcao):
        self.callbacks_restore.append(funcao)

    def vivos(self):
        with self.lock:
            return sorted(p for p in self.ultimo if p not in self.suspeitos)

    def start(self):
        agora = time.monotonic()
        with self.lock:
            # todo peer começa com o prazo de `timeout` para mandar o primeiro heartbeat
            for peer in self.node.net.peers:
                self.ultimo[peer] = agora
        self.node.log.emit("detector_start", intervalo=self.intervalo, timeout=self.timeout)
        # uma thread de envio por peer: um peer isolado ou lento não atrasa os outros
        for peer in sorted(self.node.net.peers):
            self.node.spawn(lambda p=peer: self.loop_envio(p), f"heartbeat-{peer}")
        self.node.spawn(self.loop_verifica, "detector")

    def on_heartbeat(self, msg):
        with self.lock:
            self.ultimo[msg.sender] = time.monotonic()
        return {}

    def loop_envio(self, peer):
        while not self.node.parar.is_set():
            inicio = time.monotonic()
            # o heartbeat não precisa de resposta; timeout curto para não atrasar o próximo
            self.node.net.send(peer, "heartbeat", timeout=self.intervalo)
            self.node.parar.wait(max(0, self.intervalo - (time.monotonic() - inicio)))

    def loop_verifica(self):
        while not self.node.parar.wait(self.intervalo):
            self.verificar(time.monotonic())

    def verificar(self, agora):
        with self.lock:
            sem_heartbeat = {p: agora - t for p, t in self.ultimo.items()}
        for peer, tempo in sorted(sem_heartbeat.items()):
            if tempo > self.timeout and peer not in self.suspeitos:
                with self.lock:
                    self.suspeitos.add(peer)
                self.node.log.emit("suspect", peer=peer, sem_heartbeat_s=round(tempo, 3))
                self.avisar(self.callbacks_suspect, peer)
            elif tempo <= self.timeout and peer in self.suspeitos:
                with self.lock:
                    self.suspeitos.discard(peer)
                self.node.log.emit("restore", peer=peer)
                self.avisar(self.callbacks_restore, peer)

    def avisar(self, funcoes, peer):
        for f in funcoes:
            try:
                f(peer)
            except Exception as erro:
                # um erro no Bully não pode parar o detector
                self.node.log.emit("callback_error", peer=peer, error=type(erro).__name__,
                                   detail=str(erro))
