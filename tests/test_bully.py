"""Testes da eleição de líder pelo algoritmo do valentão (Bully, issue #4)."""
import time

import pytest

from src import config as cfg
from src.bully import Bully
from src.detector import HeartbeatDetector
from src.eventlog import read_events
from src.models import Message
from src.node import Node
from tests.test_infra import make_cluster

INTERVALO = 0.1      # heartbeat do detector
TIMEOUT_HB = 0.5     # detector suspeita depois disso sem heartbeat
TIMEOUT = 0.3        # Bully: espera pelo OK (e 2 * TIMEOUT pelo COORDINATOR)
LIMITE = TIMEOUT_HB + INTERVALO + 2 * TIMEOUT + 0.5   # pior caso no modelo + folga
QUIETO = 2 * TIMEOUT + 5 * INTERVALO                  # espera as eleições pendentes terminarem


def liga_bully(nodes):
    for n in nodes:
        n.detector = HeartbeatDetector(n, INTERVALO, TIMEOUT_HB)
        n.bully = Bully(n, n.detector, TIMEOUT)
    for n in nodes:
        n.detector.start()
        n.bully.start()


def lideres(nodes):
    return {n.id: n.bully.lider for n in nodes}


def espera(condicao, limite=5.0):
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.02)
    return False


def eventos(nodes, tipo, depois=0.0):
    caminhos = sorted({n.log.path for n in nodes})
    return [e for c in caminhos for e in read_events(c) if e["event"] == tipo and e["ts"] > depois]


def no_isolado(tmp_path):
    # nó 2 com peers 1 e 3 que não existem (porta 9: conexão recusada na hora)
    node = Node(cfg.NodeConfig(2, {1: "127.0.0.1:9", 3: "127.0.0.1:9"}, port=0,
                               log_dir=str(tmp_path)), echo_log=False)
    return node, Bully(node, HeartbeatDetector(node, INTERVALO, TIMEOUT_HB), TIMEOUT)


# --- unitários (sem threads de fundo) ---

def test_host_padrao_e_loopback():
    assert cfg.from_env({"NODE_ID": "1"}).bind_addr == "127.0.0.1"
    assert cfg.from_env({"NODE_ID": "1", "BIND_ADDR": "0.0.0.0"}).bind_addr == "0.0.0.0"


def test_parametros_invalidos(tmp_path):
    node, b = no_isolado(tmp_path)
    with pytest.raises(ValueError):
        Bully(node, b.detector, timeout=0)
    node.log.close()


def test_mensagens_recebidas(tmp_path):
    node, b = no_isolado(tmp_path)
    # ELECTION de um nó menor: responde OK e pede a própria eleição
    r = node.handle(Message("ELECTION", 1, 1))
    assert r.payload == {"msg": "OK"} and b.pedido == "election_recebido"
    # COORDINATOR de um nó maior: aceita
    r = node.handle(Message("COORDINATOR", 3, 2))
    assert r.payload == {"aceito": True} and b.lider == 3
    # COORDINATOR de um nó menor: rejeita e faz a própria eleição (o valentão)
    b.pedido = None
    r = node.handle(Message("COORDINATOR", 1, 3))
    assert r.payload == {"aceito": False} and b.lider == 3 and b.pedido == "coordinator_de_menor"
    node.log.close()


def test_quando_comeca_uma_eleicao(tmp_path):
    node, b = no_isolado(tmp_path)
    d = b.detector
    d.ultimo = {1: 0.0, 3: 0.0}          # estado do detector escolhido à mão
    assert b.precisa_de_eleicao() == "sem_lider"
    b.lider = 3
    assert b.precisa_de_eleicao() is None
    d.suspeitos = {3}
    assert b.precisa_de_eleicao() == "lider_suspeito"
    b.lider = 2                           # este nó é o líder...
    assert b.precisa_de_eleicao() is None
    d.suspeitos = set()                   # ...mas o nó 3, maior, está vivo
    assert b.precisa_de_eleicao() == "no_maior_vivo"
    node.log.close()


def test_sem_ok_de_nenhum_maior_vira_lider(tmp_path):
    node, b = no_isolado(tmp_path)
    b.eleicao("teste")                    # nó 3 não responde: o nó 2 vence
    assert b.lider == 2
    fim = [e for e in read_events(node.log.path) if e["event"] == "election_end"]
    assert fim[-1]["data"] == {"lider": 2, "msgs": {"ELECTION": 1, "OK": 0, "COORDINATOR": 2}}
    node.log.close()


# --- integração (nós reais trocando mensagens por HTTP) ---

def test_cenario_normal_lider_e_o_maior_id(tmp_path):
    # os 4 nós começam ao mesmo tempo sem líder: eleições concorrentes
    nodes = make_cluster(tmp_path, n=4)
    liga_bully(nodes)
    ok = espera(lambda: lideres(nodes) == {1: 4, 2: 4, 3: 4, 4: 4})
    time.sleep(QUIETO)
    final = lideres(nodes)
    for n in nodes:
        n.stop()
    assert ok and final == {1: 4, 2: 4, 3: 4, 4: 4}
    # eleições concorrentes: pelo menos dois nós começaram a eleger antes do primeiro líder
    primeiro_lider = min(e["ts"] for e in eventos(nodes, "leader"))
    assert len({e["node"] for e in eventos(nodes, "election_start") if e["ts"] < primeiro_lider}) >= 2
    assert {e["data"]["lider"] for e in eventos(nodes, "leader")} == {4}   # ninguém decidiu outro


def test_lider_cai_novo_lider_e_anunciado_e_maior_id_volta(tmp_path):
    nodes = make_cluster(tmp_path, n=4)
    liga_bully(nodes)
    assert espera(lambda: lideres(nodes) == {1: 4, 2: 4, 3: 4, 4: 4})
    time.sleep(QUIETO)

    # crash do líder (nó 4): o nó 3 é eleito e anunciado aos nós 1 e 2
    porta = nodes[3].port
    nodes[3].stop()
    t_crash = time.time()
    vivos = nodes[:3]
    assert espera(lambda: lideres(vivos) == {1: 3, 2: 3, 3: 3}, LIMITE)
    assert {e["data"]["lider"] for e in eventos(vivos, "leader", t_crash)} == {3}

    # o nó 4 volta (processo novo, sem estado, mesma porta): faz eleição e toma a liderança.
    # Detector e Bully antes do start, como em main(): nenhum ELECTION chega sem handler
    novo = Node(cfg.NodeConfig(4, {n.id: f"127.0.0.1:{n.port}" for n in vivos}, port=porta,
                               seed=4, log_dir=str(tmp_path)), echo_log=False)
    novo.detector = HeartbeatDetector(novo, INTERVALO, TIMEOUT_HB)
    novo.bully = Bully(novo, novo.detector, TIMEOUT)
    novo.start()
    todos = vivos + [novo]
    assert espera(lambda: lideres(todos) == {1: 4, 2: 4, 3: 4, 4: 4})
    time.sleep(QUIETO)
    final = lideres(todos)
    for n in todos:
        n.stop()
    assert final == {1: 4, 2: 4, 3: 4, 4: 4}

    # o contador dos eventos "election_end" bate com as mensagens do log da rede
    total = {"ELECTION": 0, "OK": 0, "COORDINATOR": 0}
    for e in eventos(todos, "election_end"):
        for tipo, k in e["data"]["msgs"].items():
            total[tipo] += k
    enviados = [e["data"]["msg"] for e in eventos(todos, "send")]
    recebidos = [e["data"]["msg"] for e in eventos(todos, "recv")]
    assert total == {"ELECTION": enviados.count("ELECTION"), "OK": recebidos.count("ELECTION"),
                     "COORDINATOR": enviados.count("COORDINATOR")}
