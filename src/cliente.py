# Cliente e gerador de carga do serviço de tarefas (issue #8, src/tarefas.py).
#
# Carga aberta: manda `taxa` tarefas por segundo durante `duracao_s`, cada uma numa thread, no
# mesmo ritmo, responda o serviço ou não. Para cada tarefa, repete o pedido TASK até "feita":
#   - "nao_sou_lider" com outro líder: pede a esse líder na hora;
#   - sem resposta (nó caído), "falhou" (sem workers) ou sem líder: tenta o próximo nó depois
#     de `espera_s`;
#   - desiste depois de `desistir_s` sem confirmação.
# O palpite de quem é o líder é compartilhado pelas threads. O timeout de um pedido é maior que
# o tempo máximo que o líder segura uma tarefa (um EXEC de até ms + RPC_TIMEOUT a cada um dos
# n - 1 workers), então o cliente só pede de novo depois que o pedido anterior terminou.
#
# Log: <LOG_DIR>/cliente.jsonl, uma linha JSON por evento: "carga_inicio", "tarefa_enviada" (id),
# "tarefa_confirmada" (id, latencia_s, pedidos, lider, worker), "tarefa_desistiu" (id, pedidos)
# e "carga_fim". Com `eco`, imprime a cada segundo quantas tarefas foram confirmadas e a que nó o
# cliente está pedindo (para acompanhar uma demonstração: docker compose logs -f cliente).
#
# Para rodar (no compose, é o serviço "cliente", perfil "integracao"):
#   PEERS="1=127.0.0.1:8001,2=127.0.0.1:8002,3=127.0.0.1:8003" python -m src.cliente
# Variáveis: PEERS (todos os nós), TAXA (20 tarefas/s), DURACAO_S (30), TAREFA_MS (100),
# RPC_TIMEOUT (1.0, o dos nós), LOG_DIR (results/logs).
import json
import os
import threading
import time
import urllib.request

from .config import parse_peers


class Cliente:
    def __init__(self, nos, log_dir, tarefa_ms=100, rpc_timeout=1.0, espera_s=0.1, desistir_s=30.0,
                 eco=False):
        self.nos = dict(nos)                # id -> "host:porta"
        self.ids = sorted(self.nos)
        self.tarefa_ms = tarefa_ms
        self.timeout = len(self.ids) * (tarefa_ms / 1000 + rpc_timeout)
        self.espera_s = espera_s
        self.desistir_s = desistir_s
        self.alvo = self.ids[-1]            # palpite do líder: no começo, o maior ID
        self.confirmadas = 0
        self.eco = eco
        self.lock = threading.Lock()
        os.makedirs(log_dir, exist_ok=True)
        self.arquivo = open(os.path.join(log_dir, "cliente.jsonl"), "a", encoding="utf-8")

    def log(self, event, **data):
        linha = json.dumps({"ts": time.time(), "node": 0, "event": event, "data": data}, sort_keys=True)
        with self.lock:
            self.arquivo.write(linha + "\n")
            self.arquivo.flush()

    def pedir(self, no, payload):
        corpo = json.dumps({"type": "TASK", "sender": 0, "clock": 0, "payload": payload}).encode("utf-8")
        req = urllib.request.Request(f"http://{self.nos[no]}/msg", data=corpo, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))["payload"]
        except Exception:
            return None     # nó caído, timeout, resposta inválida...

    def trocar(self, de, para):
        # só troca se ninguém trocou antes: várias threads falham juntas quando o líder cai
        with self.lock:
            if self.alvo == de:
                self.alvo = para

    def tarefa(self, k):
        inicio = time.time()
        self.log("tarefa_enviada", id=k)
        pedidos = 0
        while time.time() - inicio < self.desistir_s:
            alvo = self.alvo
            pedidos += 1
            r = self.pedir(alvo, {"id": k, "ms": self.tarefa_ms, "pedido": pedidos}) or {}
            if r.get("status") == "feita":
                with self.lock:
                    self.confirmadas += 1
                self.log("tarefa_confirmada", id=k, latencia_s=round(time.time() - inicio, 4),
                         pedidos=pedidos, lider=alvo, worker=r["worker"])
                return
            if r.get("status") == "nao_sou_lider" and r.get("lider") in self.nos:
                self.trocar(alvo, r["lider"])   # o líder de um nó é sempre maior que ele: não fica em ciclo
                continue
            self.trocar(alvo, self.ids[(self.ids.index(alvo) + 1) % len(self.ids)])
            time.sleep(self.espera_s)
        self.log("tarefa_desistiu", id=k, pedidos=pedidos)

    def rodar(self, taxa=20.0, duracao_s=30.0):
        total = int(taxa * duracao_s)
        self.log("carga_inicio", taxa=taxa, duracao_s=duracao_s, tarefa_ms=self.tarefa_ms,
                 timeout=self.timeout, nos=self.ids)
        t0 = time.monotonic()
        threads = []
        for k in range(total):
            espera = t0 + k / taxa - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            if self.eco and k % max(1, round(taxa)) == 0:
                print(f"[cliente] +{k / taxa:.0f}s: {self.confirmadas}/{k} tarefas confirmadas, "
                      f"pedindo ao nó {self.alvo}", flush=True)
            t = threading.Thread(target=self.tarefa, args=(k,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        self.log("carga_fim", tarefas=total)
        self.arquivo.close()
        if self.eco:
            print(f"[cliente] fim: {self.confirmadas}/{total} tarefas confirmadas", flush=True)


def main():
    env = os.environ
    cliente = Cliente(parse_peers(env["PEERS"]), env.get("LOG_DIR", "results/logs"),
                      tarefa_ms=float(env.get("TAREFA_MS", "100")),
                      rpc_timeout=float(env.get("RPC_TIMEOUT", "1.0")), eco=True)
    cliente.rodar(float(env.get("TAXA", "20")), float(env.get("DURACAO_S", "30")))


if __name__ == "__main__":
    main()
