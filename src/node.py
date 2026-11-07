# Nó genérico: sobe um servidor HTTP e entrega cada mensagem recebida para o
# handler registrado para aquele tipo. Os algoritmos (Bully, detector de falhas)
# usam node.register("TIPO", funcao) e node.net.send(...).
#
# Endpoints:
#   POST /msg      recebe uma Message em JSON e responde com outra Message
#   POST /control  recebe comandos de injeção de falhas (block, unblock, set_faults, etc.)
#   GET  /health   usado pelo healthcheck do Docker
#   GET  /status   consulta estado do nó e falhas ativas
#
# Para rodar: python -m src.node   (configuração por variáveis de ambiente)
import json
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .bully import Bully
from .config import NetFaults
from .detector import HeartbeatDetector
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
        self.detector = None               # detector de falhas (src/detector.py), criado em main()
        self.bully = None                  # eleição de líder (src/bully.py), criada em main()
        self.register("ping", self.on_ping)

    def on_ping(self, msg):
        return {"pong": self.id}

    def register(self, msg_type, handler):
        self.handlers[msg_type] = handler

    def spawn(self, funcao, nome):
        # roda uma função em uma thread de fundo
        t = threading.Thread(target=funcao, name=nome, daemon=True)
        t.start()

    def salvar_estado(self, nome, dados):
        # Salva um dicionário em disco (persistência para crash-recovery)
        pasta = os.path.join(self.cfg.log_dir, "state")
        os.makedirs(pasta, exist_ok=True)
        caminho = os.path.join(pasta, f"node{self.id}_{nome}.json")
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(dados, f)
        self.log.emit("state_saved", name=nome)

    def carregar_estado(self, nome):
        # Lê o estado salvo anteriormente (se existir)
        caminho = os.path.join(self.cfg.log_dir, "state", f"node{self.id}_{nome}.json")
        if not os.path.exists(caminho):
            return None
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        self.log.emit("state_loaded", name=nome)
        return dados

    def handle(self, msg):
        # chamado para cada mensagem recebida
        self.clock.update(msg.clock)
        self.log.emit("recv", peer=msg.sender, msg=msg.type)
        handler = self.handlers.get(msg.type)
        if handler is None:
            self.log.emit("unknown_msg", peer=msg.sender, msg=msg.type)
            return Message("error", self.id, self.clock.tick(), {"error": "unknown_type"})
        try:
            resultado = handler(msg)
        except Exception as erro:
            # sem isso a exceção derruba a conexão e não aparece no log JSONL
            self.log.emit("handler_error", peer=msg.sender, msg=msg.type,
                          error=type(erro).__name__, detail=str(erro))
            return Message("error", self.id, self.clock.tick(), {"error": "handler_exception"})
        if resultado is None:
            resultado = {}
        return Message("ok", self.id, self.clock.tick(), resultado)

    def handle_control(self, cmd):
        # Endpoint de controle usado pelo injetor de falhas
        action = cmd.get("action")
        self.log.emit("control_received", action=action, cmd=cmd)

        if action == "block":
            peer = int(cmd["peer"])
            self.net.block(peer)
            return {"ok": True, "action": "block", "peer": peer}

        elif action == "unblock":
            peer = int(cmd["peer"])
            self.net.unblock(peer)
            return {"ok": True, "action": "unblock", "peer": peer}

        elif action == "set_faults":
            novas_falhas = NetFaults(
                drop_prob=float(cmd.get("drop_prob", self.net.faults.drop_prob)),
                delay_ms=float(cmd.get("delay_ms", self.net.faults.delay_ms)),
                jitter_ms=float(cmd.get("jitter_ms", self.net.faults.jitter_ms)),
                dup_prob=float(cmd.get("dup_prob", self.net.faults.dup_prob)),
            )
            self.net.set_faults(novas_falhas)
            return {"ok": True, "action": "set_faults", "faults": self.net.get_status()}

        elif action == "reset_faults":
            self.net.reset_faults()
            return {"ok": True, "action": "reset_faults"}

        elif action == "status":
            return {"ok": True, "status": self.net.get_status()}

        return {"ok": False, "error": f"acao_desconhecida: {action}"}

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
                elif self.path == "/status":
                    self.responder(200, {
                        "node": node.id,
                        "up": True,
                        "clock": node.clock.valor,
                        "lider": node.bully.lider if node.bully is not None else None,
                        "faults": node.net.get_status()
                    })
                else:
                    self.responder(404, {"error": "not_found"})

            def do_POST(self):
                if self.path == "/msg":
                    try:
                        tamanho = int(self.headers.get("Content-Length", "0"))
                        texto = self.rfile.read(tamanho).decode("utf-8")
                        msg = Message.from_dict(json.loads(texto))
                    except (ValueError, KeyError):
                        self.responder(400, {"error": "bad_request"})
                        return
                    self.responder(200, node.handle(msg).to_dict())

                elif self.path == "/control":
                    try:
                        tamanho = int(self.headers.get("Content-Length", "0"))
                        texto = self.rfile.read(tamanho).decode("utf-8")
                        cmd = json.loads(texto)
                    except (ValueError, KeyError):
                        self.responder(400, {"error": "bad_request"})
                        return
                    resultado = node.handle_control(cmd)
                    self.responder(200, resultado)

                else:
                    self.responder(404, {"error": "not_found"})

        ThreadingHTTPServer.daemon_threads = True
        # HOST: 127.0.0.1 por padrão (o /control não tem autenticação); no contêiner o
        # compose usa 0.0.0.0 para a rede interna e publica as portas só em 127.0.0.1
        return ThreadingHTTPServer((self.cfg.host, self.cfg.port), Handler)

    @property
    def port(self):
        # porta real (nos testes usamos porta 0 e o sistema escolhe uma livre)
        if self.server is not None:
            return self.server.server_address[1]
        return self.cfg.port

    def start(self):
        self.server = self.criar_servidor()
        self.spawn(self.server.serve_forever, "http")
        self.log.emit("node_start", port=self.port, peers=sorted(self.cfg.peers), seed=self.cfg.seed,
                      incarnation=self.log.incarnation)
        if self.cfg.ping_interval > 0:
            self.spawn(self.loop_ping, "ping")
        if self.detector is not None:
            self.detector.start()
        if self.bully is not None:
            self.bully.start()

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
    cfg = config.from_env()
    node = Node(cfg)
    if cfg.hb_interval > 0:
        # o Bully depende do detector para saber se o líder caiu
        node.detector = HeartbeatDetector(node, cfg.hb_interval, cfg.hb_timeout)
        node.bully = Bully(node, node.detector, cfg.rpc_timeout)
    node.run_forever()


if __name__ == "__main__":
    main()
