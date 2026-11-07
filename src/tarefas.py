# Serviço de tarefas sobre o detector de falhas (#3) e o Bully (#4), issue #8.
#
# Problema: um despachante de tarefas, como um escalonador de jobs em lote. O cliente
# (src/cliente.py) manda tarefas; o líder eleito pelo Bully é o coordenador: distribui cada
# tarefa, em rodízio, a um worker (os outros nós) que o detector vê vivo; o worker executa e
# responde; o líder confirma ao cliente. Não há algoritmo novo: só detector.vivos() e bully.lider.
#
# Mensagens (POST /msg, com timeout):
#   TASK  cliente -> líder  {"id", "ms", "pedido"}: executar a tarefa `id`, que leva `ms` ms
#                           ("pedido" numera os pedidos do cliente para a mesma tarefa).
#                           Resposta: {"status": "feita", "worker"}, {"status": "falhou"}
#                           ou {"status": "nao_sou_lider", "lider"}
#   EXEC  líder -> worker   a mesma tarefa. Resposta {"feita": True} depois de executar, ou
#                           {"feita": False} se quem pediu não é o líder do worker
#
# Falhas:
#   - worker cai (EXEC sem resposta: conexão recusada ou timeout) ou recusa: o líder reatribui a
#     tarefa a outro worker vivo, cada worker no máximo uma vez por pedido; sem nenhum, "falhou";
#   - líder cai: o cliente fica sem resposta e reenvia ao novo líder. É a semântica "pelo menos
#     uma vez": uma tarefa que estava em execução quando o líder caiu pode rodar de novo.
# Nada é persistido: quem guarda cada tarefa até a confirmação é o cliente.
#
# Log: no líder, "task_assign" (task, pedido, worker) a cada atribuição e "task_reply" (task,
# pedido, status); no worker, "task_start"/"task_end" (task, pedido, lider) e "task_rejected".
# Modelo do sistema e propriedades: docs/integracao.md.
import threading
import time


class Despachante:
    def __init__(self, node, detector, bully, timeout=1.0):
        self.node = node
        self.detector = detector
        self.bully = bully
        self.timeout = timeout      # espera pelo worker além da duração da tarefa (RPC_TIMEOUT)
        self.proximo = 0            # rodízio entre os workers: avança uma vez por pedido
        self.lock = threading.Lock()
        node.register("TASK", self.on_task)
        node.register("EXEC", self.on_exec)

    def eh_lider(self):
        return self.bully.lider == self.node.id

    def on_task(self, msg):
        # do cliente: só o líder despacha
        if not self.eh_lider():
            return {"status": "nao_sou_lider", "lider": self.bully.lider}
        tarefa = {k: msg.payload[k] for k in ("id", "ms", "pedido")}
        ids = {"task": tarefa["id"], "pedido": tarefa["pedido"]}
        with self.lock:
            self.proximo += 1
            vez = self.proximo
        tentados = []
        while self.eh_lider():
            workers = [w for w in self.detector.vivos() if w not in tentados]
            if not workers:
                break
            w = workers[vez % len(workers)]
            tentados.append(w)
            self.node.log.emit("task_assign", worker=w, **ids)
            r = self.node.net.send(w, "EXEC", tarefa, timeout=tarefa["ms"] / 1000 + self.timeout)
            if r is not None and r.payload.get("feita"):
                self.node.log.emit("task_reply", status="feita", worker=w, **ids)
                return {"status": "feita", "worker": w}
        self.node.log.emit("task_reply", status="falhou", tentados=tentados, **ids)
        return {"status": "falhou"}

    def on_exec(self, msg):
        # do líder: o worker só executa para o líder que ele conhece, então um líder antigo,
        # que ainda não soube do novo, não consegue despachar
        t = msg.payload
        ids = {"task": t["id"], "pedido": t["pedido"], "lider": msg.sender}
        if msg.sender != self.bully.lider:
            self.node.log.emit("task_rejected", meu_lider=self.bully.lider, **ids)
            return {"feita": False}
        self.node.log.emit("task_start", **ids)
        time.sleep(t["ms"] / 1000)      # o trabalho da tarefa (simulado)
        self.node.log.emit("task_end", **ids)
        return {"feita": True}
