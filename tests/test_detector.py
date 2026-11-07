"""Testes do detector de falhas por heartbeat (issue #3)."""
import time

import pytest

from src import config as cfg
from src.detector import HeartbeatDetector
from src.eventlog import read_events
from src.models import Message
from src.node import Node
from tests.test_infra import make_cluster

INTERVALO = 0.1
TIMEOUT = 0.5
LIMITE = TIMEOUT + INTERVALO + 0.3   # pior caso do detector + folga para o escalonador


def liga_detectores(nodes, avisos):
    # avisos recebe (observador, "suspect"/"restore", peer, instante)
    for n in nodes:
        n.detector = HeartbeatDetector(n, INTERVALO, TIMEOUT)
        n.detector.on_suspect(lambda p, i=n.id: avisos.append((i, "suspect", p, time.monotonic())))
        n.detector.on_restore(lambda p, i=n.id: avisos.append((i, "restore", p, time.monotonic())))
    for n in nodes:
        n.detector.start()


def espera(condicao, limite=5.0):
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.02)
    return False


# --- unitários (sem threads: chamam verificar() com instantes escolhidos) ---

def test_parametros_invalidos(tmp_path):
    node = Node(cfg.NodeConfig(1, {}, port=0, log_dir=str(tmp_path)), echo_log=False)
    with pytest.raises(ValueError):
        HeartbeatDetector(node, intervalo=1.0, timeout=1.0)
    with pytest.raises(ValueError):
        HeartbeatDetector(node, intervalo=0, timeout=1.0)


def test_suspeita_e_restauracao(tmp_path):
    node = Node(cfg.NodeConfig(1, {2: "x:1"}, port=0, log_dir=str(tmp_path)), echo_log=False)
    d = HeartbeatDetector(node, intervalo=1.0, timeout=3.0)
    avisos = []
    d.on_suspect(lambda p: avisos.append(("suspect", p)))
    d.on_suspect(lambda p: 1 / 0)                     # callback com bug não para o detector
    d.on_restore(lambda p: avisos.append(("restore", p)))
    d.ultimo = {2: 100.0}

    d.verificar(102.9)                                # ainda dentro do timeout
    assert avisos == [] and d.vivos() == [2]
    d.verificar(103.5)
    assert avisos == [("suspect", 2)] and d.vivos() == []
    d.verificar(104.0)                                # não avisa de novo
    assert avisos == [("suspect", 2)]

    node.handle(Message("heartbeat", 2, 1))           # chega um heartbeat do peer 2
    d.verificar(time.monotonic())
    assert avisos == [("suspect", 2), ("restore", 2)] and d.vivos() == [2]

    eventos = [e["event"] for e in read_events(node.log.path)]
    assert "callback_error" in eventos
    assert eventos.index("suspect") < eventos.index("restore")
    node.log.close()


# --- integração (nós reais trocando heartbeats por HTTP) ---

def test_rede_estavel_sem_falso_positivo(tmp_path):
    nodes = make_cluster(tmp_path, n=3)
    avisos = []
    liga_detectores(nodes, avisos)
    time.sleep(2.0)                                   # 20 intervalos
    vivos = {n.id: n.detector.vivos() for n in nodes}
    for n in nodes:
        n.stop()
    assert avisos == []
    assert vivos == {1: [2, 3], 2: [1, 3], 3: [1, 2]}
    for n in nodes:
        assert not any(e["event"] == "suspect" for e in read_events(n.log.path))


def test_crash_detectado_por_todos_e_recuperacao(tmp_path):
    nodes = make_cluster(tmp_path, n=3)
    avisos = []
    liga_detectores(nodes, avisos)
    time.sleep(0.5)

    # crash do nó 3: para de mandar heartbeats e de responder
    porta = nodes[2].port
    inicio = time.monotonic()
    nodes[2].stop()
    assert espera(lambda: {i for i, tipo, p, _ in avisos if tipo == "suspect" and p == 3} == {1, 2})
    for i, tipo, p, t in avisos:
        assert tipo == "suspect" and p == 3          # ninguém suspeitou de um nó correto
        assert t - inicio <= LIMITE

    # recuperação: o nó 3 volta (processo novo, mesma porta) e é restaurado por todos
    novo = Node(cfg.NodeConfig(node_id=3, peers={}, port=porta, seed=3, log_dir=str(tmp_path)),
                echo_log=False)
    novo.start()
    novo.net.peers = {n.id: f"127.0.0.1:{n.port}" for n in nodes[:2]}
    liga_detectores([novo], avisos)
    assert espera(lambda: {i for i, tipo, p, _ in avisos if tipo == "restore" and p == 3} == {1, 2})
    time.sleep(3 * INTERVALO)
    vivos = {n.id: n.detector.vivos() for n in nodes[:2] + [novo]}
    for n in nodes[:2] + [novo]:
        n.stop()
    assert vivos == {1: [2, 3], 2: [1, 3], 3: [1, 2]}
    assert not any(i == 3 for i, *_ in avisos)       # o nó que voltou não suspeitou de ninguém
