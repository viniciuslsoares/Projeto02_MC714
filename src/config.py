# Configuração de cada nó. Tudo vem de variáveis de ambiente, assim a mesma
# imagem Docker serve para qualquer nó (só muda o NODE_ID).
#
# NODE_ID        id do nó (obrigatório)
# PEERS          outros nós no formato "1=node1:8000,2=node2:8000"
# PORT           porta HTTP do nó (padrão 8000)
# SEED           semente do experimento (padrão 0); a do nó é SEED * 1000 + NODE_ID
# LOG_DIR        pasta dos logs (padrão results/logs)
# RPC_TIMEOUT    timeout das chamadas de rede, em segundos (padrão 1.0)
# PING_INTERVAL  intervalo do ping entre nós; 0 desliga (padrão 0)
# HB_INTERVAL    intervalo do heartbeat do detector de falhas; 0 desliga (padrão 0)
# HB_TIMEOUT     tempo sem heartbeat para suspeitar de um peer, em segundos (padrão 2.0)
# NET_DROP_PROB, NET_DELAY_MS, NET_JITTER_MS, NET_DUP_PROB: falhas de rede simuladas
import os
from dataclasses import dataclass, field


@dataclass
class NetFaults:
    # falhas simuladas no envio de mensagens
    drop_prob: float = 0.0    # chance de perder a mensagem
    delay_ms: float = 0.0     # atraso fixo
    jitter_ms: float = 0.0    # atraso aleatório extra (de 0 até esse valor)
    dup_prob: float = 0.0     # chance de enviar a mensagem duas vezes


@dataclass
class NodeConfig:
    node_id: int
    peers: dict                 # id -> "host:porta"
    port: int = 8000
    seed: int = 0
    log_dir: str = "results/logs"
    rpc_timeout: float = 1.0
    ping_interval: float = 0.0
    hb_interval: float = 0.0
    hb_timeout: float = 2.0
    faults: NetFaults = field(default_factory=NetFaults)


def parse_peers(texto, meu_id=None):
    # transforma "1=a:80,2=b:80" em {1: "a:80", 2: "b:80"}, sem o próprio nó
    peers = {}
    for item in texto.split(","):
        item = item.strip()
        if item == "":
            continue
        if "=" not in item:
            raise ValueError("peer inválido: " + item + " (use id=host:porta)")
        id_texto, endereco = item.split("=", 1)
        if int(id_texto) != meu_id:
            peers[int(id_texto)] = endereco
    return peers


def from_env(env=None):
    if env is None:
        env = os.environ
    node_id = int(env["NODE_ID"])
    faults = NetFaults(
        drop_prob=float(env.get("NET_DROP_PROB", "0")),
        delay_ms=float(env.get("NET_DELAY_MS", "0")),
        jitter_ms=float(env.get("NET_JITTER_MS", "0")),
        dup_prob=float(env.get("NET_DUP_PROB", "0")),
    )
    return NodeConfig(
        node_id=node_id,
        peers=parse_peers(env.get("PEERS", ""), node_id),
        port=int(env.get("PORT", "8000")),
        seed=int(env.get("SEED", "0")) * 1000 + node_id,
        log_dir=env.get("LOG_DIR", "results/logs"),
        rpc_timeout=float(env.get("RPC_TIMEOUT", "1.0")),
        ping_interval=float(env.get("PING_INTERVAL", "0")),
        hb_interval=float(env.get("HB_INTERVAL", "0")),
        hb_timeout=float(env.get("HB_TIMEOUT", "2.0")),
        faults=faults,
    )
