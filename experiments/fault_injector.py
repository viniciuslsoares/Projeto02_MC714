# Injetor de falhas determinístico: lê cenários em YAML e comanda as falhas
# em tempo de execução via HTTP (/control) e chamadas do Docker (crash/recover).
#
# Uso direto:
#   python -m experiments.fault_injector scenarios/particao_rede.yaml
import json
import subprocess
import sys
import time
import urllib.request
import yaml


def carregar_cenario(caminho_yaml):
    # Lê o arquivo de cenário YAML
    with open(caminho_yaml, "r", encoding="utf-8") as f:
        dados = yaml.safe_load(f)

    # Validação simples dos campos obrigatórios
    if "events" not in dados:
        raise ValueError("O cenário precisa ter uma lista de 'events'")
    dados["events"] = sorted(dados["events"], key=lambda e: e.get("time_s", 0.0))
    return dados


class FaultInjector:
    def __init__(self, node_addrs=None, modo_docker=True, on_crash=None, on_recover=None):
        # node_addrs: dicionário {id: "host:porta"}
        # Se for None no modo docker, usa nomes padrões dos contêineres ou portas mapeadas
        self.node_addrs = node_addrs or {
            1: "127.0.0.1:8001",
            2: "127.0.0.1:8002",
            3: "127.0.0.1:8003",
        }
        self.modo_docker = modo_docker
        self.on_crash = on_crash
        self.on_recover = on_recover

    def enviar_comando(self, node_id, cmd):
        # Envia requisição POST para o /control do nó
        if node_id not in self.node_addrs:
            return None
        url = f"http://{self.node_addrs[node_id]}/control"
        dados = json.dumps(cmd).encode("utf-8")
        req = urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"  [Aviso] Falha ao enviar comando para nó {node_id}: {e}")
            return None

    def executar_evento(self, evento):
        acao = evento.get("action")
        t = evento.get("time_s", 0.0)

        if acao == "partition":
            nodes = evento.get("nodes", [])
            target = evento.get("target")
            print(f"[{t:05.2f}s] [FALHA] Partição criada: nós {nodes} <-> nó {target}")
            # Isola nós do target e o target dos nós (partição bidirecional)
            for n in nodes:
                self.enviar_comando(n, {"action": "block", "peer": target})
            if target:
                for n in nodes:
                    self.enviar_comando(target, {"action": "block", "peer": n})

        elif acao == "heal":
            nodes = evento.get("nodes", [])
            target = evento.get("target")
            print(f"[{t:05.2f}s] [CURA] Partição removida: nós {nodes} <-> nó {target}")
            for n in nodes:
                self.enviar_comando(n, {"action": "unblock", "peer": target})
            if target:
                for n in nodes:
                    self.enviar_comando(target, {"action": "unblock", "peer": n})

        elif acao == "crash":
            node_id = evento.get("node")
            print(f"[{t:05.2f}s] [FALHA] Crash-stop no nó {node_id}")
            if self.on_crash:
                self.on_crash(node_id)
            elif self.modo_docker:
                nome_container = f"mc714-node{node_id}"
                subprocess.run(["docker", "stop", nome_container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        elif acao == "recover":
            node_id = evento.get("node")
            print(f"[{t:05.2f}s] [RECUPERACAO] Crash-recovery no nó {node_id}")
            if self.on_recover:
                self.on_recover(node_id)
            elif self.modo_docker:
                nome_container = f"mc714-node{node_id}"
                subprocess.run(["docker", "start", nome_container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        elif acao == "set_faults":
            node_id = evento.get("node")
            cmd = {
                "action": "set_faults",
                "drop_prob": evento.get("drop_prob", 0.0),
                "delay_ms": evento.get("delay_ms", 0.0),
                "jitter_ms": evento.get("jitter_ms", 0.0),
                "dup_prob": evento.get("dup_prob", 0.0),
            }
            print(f"[{t:05.2f}s] [FALHA] Degradação de rede no nó {node_id}: {cmd}")
            self.enviar_comando(node_id, cmd)

        elif acao == "reset_faults":
            node_id = evento.get("node")
            if node_id:
                print(f"[{t:05.2f}s] [CURA] Reset de falhas no nó {node_id}")
                self.enviar_comando(node_id, {"action": "reset_faults"})
            else:
                print(f"[{t:05.2f}s] [CURA] Reset de falhas em todos os nós")
                for nid in self.node_addrs:
                    self.enviar_comando(nid, {"action": "reset_faults"})

    def run(self, cenario):
        nome = cenario.get("name", "cenario")
        duracao = float(cenario.get("duration_s", 10.0))
        eventos = cenario.get("events", [])

        print(f"=== Iniciando Cenário: {nome} (duração: {duracao}s) ===")
        t_inicio = time.time()

        for ev in eventos:
            t_alvo = float(ev.get("time_s", 0.0))
            espera = t_alvo - (time.time() - t_inicio)
            if espera > 0:
                time.sleep(espera)
            self.executar_evento(ev)

        # Aguarda o restante da duração total do cenário
        restante = duracao - (time.time() - t_inicio)
        if restante > 0:
            time.sleep(restante)

        # Ao final, restaura a rede para limpa
        print("=== Cenário concluído. Resetando falhas em todos os nós ===")
        for nid in self.node_addrs:
            self.enviar_comando(nid, {"action": "reset_faults"})


def main():
    if len(sys.argv) < 2:
        print("Uso: python -m experiments.fault_injector <cenario.yaml>")
        sys.exit(1)

    caminho = sys.argv[1]
    cenario = carregar_cenario(caminho)
    injetor = FaultInjector(modo_docker=True)
    injetor.run(cenario)


if __name__ == "__main__":
    main()
