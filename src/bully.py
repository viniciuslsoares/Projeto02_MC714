# Eleição de líder pelo algoritmo do valentão (Bully), issue #4.
#
# Mensagens (todas por node.net.send, com timeout explícito):
#   ELECTION     vai para os nós de ID maior
#   OK           resposta ao ELECTION (a própria resposta HTTP): "estou vivo e sou
#                maior, deixa comigo"; quem responde faz a sua própria eleição
#   COORDINATOR  o vencedor avisa todos os nós de que é o novo líder
#
# Eleição no nó i:
#   1. manda ELECTION a todos os nós maiores que i (em paralelo) e espera até `timeout`
#   2. nenhum OK -> i vence: líder = i e manda COORDINATOR a todos
#   3. algum OK  -> espera o COORDINATOR até `espera`; se não chegar, volta ao passo 1
#
# A thread "bully" confere a cada intervalo do detector (e logo que algo muda)
# se precisa de uma eleição:
#   - o nó não tem líder (acabou de subir ou voltou de um crash);
#   - o detector suspeita do líder;
#   - um nó maior que o líder está vivo (nó maior voltou, partição curada);
#   - chegou ELECTION de um nó menor, ou COORDINATOR de um nó menor (o "valentão"
#     não aceita um líder menor que ele: faz a própria eleição e toma o lugar).
#
# Log: "election_start" (motivo), "leader" (lider) quando o nó decide um líder,
# "election_end" (lider, msgs) e "coordinator_rejected". Contador de mensagens:
# o `msgs` de "election_end" diz quantas ELECTION, OK e COORDINATOR este nó enviou
# nessa eleição; o total de uma eleição é a soma dos "election_end" de todos os nós.
#
# O modelo do sistema e as propriedades estão em docs/modelo_bully.md.
import threading

TIPOS = ("ELECTION", "OK", "COORDINATOR")


class Bully:
    def __init__(self, node, detector, timeout=1.0):
        if timeout <= 0:
            raise ValueError("timeout deve ser maior que 0")
        self.node = node
        self.id = node.id
        self.detector = detector
        self.timeout = timeout          # espera pelo OK (e pela resposta ao COORDINATOR)
        self.espera = 2 * timeout       # espera pelo COORDINATOR depois de receber um OK
        self.lider = None
        self.em_eleicao = False
        self.pedido = None              # motivo de uma eleição pedida por mensagem recebida
        self.contagem = dict.fromkeys(TIPOS, 0)
        self.lock = threading.Lock()
        self.acordar = threading.Event()       # acorda a thread "bully" antes do intervalo
        self.coordinator = threading.Event()   # chegou COORDINATOR durante a eleição
        node.register("ELECTION", self.on_election)
        node.register("COORDINATOR", self.on_coordinator)
        detector.on_suspect(lambda peer: self.acordar.set())
        detector.on_restore(lambda peer: self.acordar.set())

    def start(self):
        self.node.spawn(self.loop, "bully")

    def loop(self):
        while not self.node.parar.is_set():
            self.acordar.clear()
            motivo = self.precisa_de_eleicao()
            if motivo is not None:
                self.eleicao(motivo)
            self.acordar.wait(self.detector.intervalo)

    def precisa_de_eleicao(self):
        vivos = self.detector.vivos()
        with self.lock:
            if self.pedido is not None:
                return self.pedido
            if self.lider is None:
                return "sem_lider"
            if self.lider != self.id and self.lider not in vivos:
                return "lider_suspeito"
            if any(p > self.lider for p in vivos):
                return "no_maior_vivo"
        return None

    def eleicao(self, motivo):
        with self.lock:
            self.em_eleicao = True
            self.pedido = None
            self.coordinator.clear()
        try:
            while not self.node.parar.is_set():
                maiores = sorted(p for p in self.node.net.peers if p > self.id)
                self.node.log.emit("election_start", motivo=motivo, maiores=maiores)
                respostas = self.enviar(maiores, "ELECTION")
                if not any(r is not None and r.payload.get("msg") == "OK" for r in respostas):
                    self.virar_lider()
                    return
                # um nó maior está vivo e assume a eleição: espera o COORDINATOR dele
                if self.coordinator.wait(self.espera):
                    return
                motivo = "sem_coordinator"
        finally:
            with self.lock:
                self.em_eleicao = False
                # no mesmo lock do on_election: um OK enviado durante a eleição entra
                # nesta contagem; um OK enviado depois pede outra eleição e entra na dela
                msgs, self.contagem = self.contagem, dict.fromkeys(TIPOS, 0)
                self.node.log.emit("election_end", lider=self.lider, msgs=msgs)

    def virar_lider(self):
        outros = sorted(self.node.net.peers)
        with self.lock:
            if self.coordinator.is_set():
                return     # um nó maior se anunciou enquanto esperávamos os OKs
            self.lider = self.id
            self.node.log.emit("leader", lider=self.id)
        self.enviar(outros, "COORDINATOR")

    def enviar(self, peers, tipo):
        # em paralelo (uma thread por peer): um peer caído não atrasa os outros
        with self.lock:
            self.contagem[tipo] += len(peers)
        respostas = []
        threads = [threading.Thread(target=lambda p=p: respostas.append(
                       self.node.net.send(p, tipo, timeout=self.timeout)), daemon=True)
                   for p in peers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return respostas

    def on_election(self, msg):
        with self.lock:
            self.contagem["OK"] += 1
            self.pedir_eleicao("election_recebido")
        return {"msg": "OK"}

    def on_coordinator(self, msg):
        if msg.sender < self.id:
            # um nó menor se anunciou, mas este está vivo e é maior: faz a própria eleição
            self.node.log.emit("coordinator_rejected", peer=msg.sender)
            with self.lock:
                self.pedir_eleicao("coordinator_de_menor")
            return {"aceito": False}
        with self.lock:
            self.lider = msg.sender
            self.coordinator.set()
            self.node.log.emit("leader", lider=msg.sender)
        return {"aceito": True}

    def pedir_eleicao(self, motivo):
        # chamada com o lock. Se já há uma eleição em andamento aqui, ela termina
        # com um COORDINATOR para todos, que também responde a quem pediu
        if not self.em_eleicao:
            self.pedido = motivo
        self.acordar.set()
