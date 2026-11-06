"""Testes da infraestrutura base (issue #1): config, transporte com timeout, logs e falhas injetadas."""
import socket
import threading
import time

import pytest

from src import config as cfg
from src.eventlog import read_events
from src.models import Message
from src.node import Node


def make_cluster(tmp_path, n=3, **node_kwargs):
    nodes = []
    for i in range(1, n + 1):
        c = cfg.NodeConfig(node_id=i, peers={}, port=0, seed=i, log_dir=str(tmp_path), **node_kwargs)
        node = Node(c, echo_log=False)
        node.start()
        nodes.append(node)
    for a in nodes:
        a.net.peers = {b.id: f"127.0.0.1:{b.port}" for b in nodes if b is not a}
    return nodes


@pytest.fixture
def cluster(tmp_path):
    nodes = make_cluster(tmp_path)
    yield nodes
    for n in nodes:
        n.stop()


def test_parse_peers_ignora_proprio_no():
    assert cfg.parse_peers("1=a:1,2=b:2,3=c:3", meu_id=2) == {1: "a:1", 3: "c:3"}


def test_from_env():
    c = cfg.from_env({"NODE_ID": "2", "PEERS": "1=a:1,2=b:2", "NET_DROP_PROB": "0.25", "RPC_TIMEOUT": "0.5"})
    assert c.node_id == 2 and c.peers == {1: "a:1"}
    assert c.faults.drop_prob == 0.25 and c.rpc_timeout == 0.5


def test_ping_entre_nos(cluster):
    reply = cluster[0].net.send(2, "ping")
    assert reply is not None and reply.payload == {"pong": 2}


def test_relogio_de_lamport_avanca_na_recepcao(cluster):
    a, b = cluster[0], cluster[1]
    for _ in range(20):
        a.clock.tick()
    before = b.clock.valor
    a.net.send(2, "ping")
    assert b.clock.valor > before and b.clock.valor > 20


def test_tipo_desconhecido(cluster):
    reply = cluster[0].net.send(2, "nao_existe")
    assert reply is not None and reply.type == "error" and reply.payload["error"] == "unknown_type"
    evs = read_events(cluster[1].log.path)
    assert any(e["event"] == "unknown_msg" for e in evs)


def test_timeout_explicito_em_peer_que_nao_responde(tmp_path):
    # Servidor que aceita a conexão e nunca responde.
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    stop = threading.Event()
    conns = []
    threading.Thread(target=lambda: conns.append(srv.accept()) or stop.wait(5), daemon=True).start()
    node = Node(cfg.NodeConfig(1, {}, port=0, log_dir=str(tmp_path), rpc_timeout=0.3), echo_log=False)
    node.start()
    node.net.peers = {2: f"127.0.0.1:{srv.getsockname()[1]}"}
    t0 = time.monotonic()
    assert node.net.send(2, "ping") is None
    assert time.monotonic() - t0 < 2.0
    assert any(e["event"] == "send_failed" for e in read_events(node.log.path))
    stop.set()
    node.stop()
    srv.close()


def test_timeout_invalido_e_rejeitado(cluster):
    with pytest.raises(ValueError):
        cluster[0].net.send(2, "ping", timeout=0)


def test_peer_inexistente_falha_sem_travar(tmp_path):
    node = Node(cfg.NodeConfig(1, {}, port=0, log_dir=str(tmp_path), rpc_timeout=0.3), echo_log=False)
    node.start()
    node.net.peers = {2: "127.0.0.1:9"}
    assert node.net.send(2, "ping") is None
    node.stop()


def test_particao_bloqueia_e_cura(cluster):
    a = cluster[0]
    a.net.block(2)
    assert a.net.send(2, "ping") is None
    assert any(e["event"] == "drop" and e["data"]["reason"] == "partition" for e in read_events(a.log.path))
    a.net.unblock(2)
    assert a.net.send(2, "ping") is not None


def test_perda_total_e_reprodutivel(tmp_path):
    def run(sub):
        nodes = make_cluster(tmp_path / sub, n=2, faults=cfg.NetFaults(drop_prob=0.5))
        outcome = [nodes[0].net.send(2, "ping") is not None for _ in range(30)]
        for n in nodes:
            n.stop()
        return outcome

    r1, r2 = run("a"), run("b")
    assert r1 == r2                     # mesma semente => mesma sequência de perdas
    assert 0 < sum(r1) < 30             # nem tudo perdido, nem nada perdido


def test_atraso_injetado(tmp_path):
    nodes = make_cluster(tmp_path, n=2, faults=cfg.NetFaults(delay_ms=100))
    t0 = time.monotonic()
    assert nodes[0].net.send(2, "ping") is not None
    assert time.monotonic() - t0 >= 0.1
    for n in nodes:
        n.stop()


def test_duplicacao_entrega_duas_vezes(tmp_path):
    nodes = make_cluster(tmp_path, n=2, faults=cfg.NetFaults(dup_prob=1.0))
    nodes[0].net.send(2, "ping")
    recvs = [e for e in read_events(nodes[1].log.path) if e["event"] == "recv"]
    assert len(recvs) == 2
    for n in nodes:
        n.stop()


def test_logs_jsonl_parseaveis(cluster):
    cluster[0].net.send(2, "ping")
    for node in cluster[:2]:
        for e in read_events(node.log.path):
            assert {"ts", "node", "clock", "event", "data"} <= e.keys()


def test_message_roundtrip():
    m = Message("x", 3, 7, {"a": 1})
    assert Message.from_dict(m.to_dict()) == m


def test_endpoint_controle_bloqueio_e_falhas(cluster):
    import json
    import urllib.request

    node1, node2 = cluster[0], cluster[1]

    # Envia comando de bloqueio via HTTP POST /control
    req = urllib.request.Request(
        f"http://127.0.0.1:{node1.port}/control",
        data=json.dumps({"action": "block", "peer": 2}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=1.0) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        assert res["ok"] is True
        assert res["action"] == "block"

    # Agora o nó 1 não consegue enviar para o 2
    assert node1.net.send(2, "ping") is None

    # Desbloqueia via /control
    req_unblock = urllib.request.Request(
        f"http://127.0.0.1:{node1.port}/control",
        data=json.dumps({"action": "unblock", "peer": 2}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req_unblock, timeout=1.0) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        assert res["ok"] is True

    assert node1.net.send(2, "ping") is not None


def test_persistencia_de_estado_do_no(tmp_path):
    c = cfg.NodeConfig(node_id=1, peers={}, port=0, log_dir=str(tmp_path))
    node = Node(c, echo_log=False)
    node.salvar_estado("termo", {"termo_atual": 3, "votou_em": 2})

    # Simula reinício do nó criando uma nova instância com mesmo diretório de log
    novo_node = Node(c, echo_log=False)
    dados = novo_node.carregar_estado("termo")
    assert dados == {"termo_atual": 3, "votou_em": 2}

