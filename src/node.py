# Nó genérico: sobe um servidor HTTP e entrega cada mensagem recebida para o
# handler registrado para aquele tipo. Os algoritmos (Bully, detector de falhas)
# vão usar node.register("TIPO", funcao) e node.net.send(...).
#
# Endpoints:
#   POST /msg     recebe uma Message em JSON e responde com outra Message
#   GET  /health  usado pelo healthcheck do Docker
#
# Para rodar: python -m src.node   (configuração por variáveis de ambiente)
import json
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .eventlog import EventLog, LamportClock
from .models import Message
from .network import Network


class Node:
    def __init__(self, cfg, echo_log=True):
        self.cfg = cfg
        self.id = cfg.node_id
        self.clock = LamportClock()
        self.log = EventLog(self.id, cfg.log_dir, self.clock, echo=echo_log)
        self.net = Network(self.id, cfg.peers, self.clock, self.log,
                           default_timeout=cfg.rpc_timeout, faults=cfg.faults, seed=cfg.seed)
        self.handlers = {}                 # tipo da mensagem -> função
        self.parar = threading.Event()     # avisa as threads para terminarem
        self.server = None
        self.register("ping", self.on_ping)

    def on_ping(self, msg):
        return {"pong": self.id}

    def register(self, msg_type, handler):
        self.handlers[msg_type] = handler

    def spawn(self, funcao, nome):
        # roda uma função em uma thread de fundo
        t = threading.Thread(target=funcao, name=nome, daemon=True)
        t.start()

    def handle(self, msg):
        # chamado para cada mensagem recebida
        self.clock.update(msg.clock)
        self.log.emit("recv", peer=msg.sender, msg=msg.type)
        handler = self.handlers.get(msg.type)
        if handler is None:
            self.log.emit("unknown_msg", peer=msg.sender, msg=msg.type)
            return Message("error", self.id, self.clock.tick(), {"error": "unknown_type"})
        resultado = handler(msg)
        return Message("ok", self.id, self.clock.tick(), resultado)

    def criar_servidor(self):
        node = self    # o handler abaixo precisa acessar o nó

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass   # desliga o log padrão do http.server (usamos o nosso)

            def responder(self, codigo, objeto):
                corpo = json.dumps(objeto).encode("utf-8")
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

            def do_GET(self):
                if self.path == "/health":
                    self.responder(200, {"node": node.id, "up": True})
                else:
                    self.responder(404, {"error": "not_found"})

            def do_POST(self):
                if self.path != "/msg":
                    self.responder(404, {"error": "not_found"})
                    return
                try:
                    tamanho = int(self.headers.get("Content-Length", "0"))
                    texto = self.rfile.read(tamanho).decode("utf-8")
                    msg = Message.from_dict(json.loads(texto))
                except (ValueError, KeyError):
                    self.responder(400, {"error": "bad_request"})
                    return
                self.responder(200, node.handle(msg).to_dict())

        ThreadingHTTPServer.daemon_threads = True
        return ThreadingHTTPServer(("0.0.0.0", self.cfg.port), Handler)

    @property
    def port(self):
        # porta real (nos testes usamos porta 0 e o sistema escolhe uma livre)
        if self.server is not None:
            return self.server.server_address[1]
        return self.cfg.port

    def start(self):
        self.server = self.criar_servidor()
        self.spawn(self.server.serve_forever, "http")
        self.log.emit("node_start", port=self.port, peers=sorted(self.cfg.peers), seed=self.cfg.seed)
        if self.cfg.ping_interval > 0:
            self.spawn(self.loop_ping, "ping")

    def loop_ping(self):
        # de tempos em tempos, manda ping para todos os outros nós
        while not self.parar.wait(self.cfg.ping_interval):
            for peer_id in sorted(self.cfg.peers):
                self.net.send(peer_id, "ping")

    def stop(self):
        self.parar.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        self.log.emit("node_stop")
        self.log.close()

    def run_forever(self):
        self.start()
        terminou = threading.Event()
        # Ctrl+C ou "docker stop" encerram o nó
        signal.signal(signal.SIGINT, lambda *args: terminou.set())
        signal.signal(signal.SIGTERM, lambda *args: terminou.set())
        terminou.wait()
        self.stop()


def main():
    Node(config.from_env()).run_forever()


if __name__ == "__main__":
    main()
