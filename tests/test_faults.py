"""Testes unitários automatizados do framework de injeção de falhas (Issue #2)."""
import pytest

from experiments.fault_injector import FaultInjector, carregar_cenario
from src.eventlog import read_events
from tests.test_infra import make_cluster


@pytest.fixture
def cluster_falhas(tmp_path):
    # rpc_timeout curto: envios sob partição ou perda esperam o timeout inteiro
    nodes = make_cluster(tmp_path, n=3, rpc_timeout=0.1)
    yield nodes
    for n in nodes:
        n.stop()


def test_carregar_cenarios_declarativos():
    c1 = carregar_cenario("scenarios/particao_rede.yaml")
    assert c1["name"] == "particao_rede"
    assert c1["duration_s"] == 12.0
    assert len(c1["events"]) == 2

    c2 = carregar_cenario("scenarios/queda_e_recuperacao.yaml")
    assert c2["name"] == "queda_e_recuperacao"
    assert len(c2["events"]) == 4


def test_carregar_cenario_invalido(tmp_path):
    arquivo = tmp_path / "invalido.yaml"
    arquivo.write_text("name: sem_eventos\n", encoding="utf-8")
    with pytest.raises(ValueError):
        carregar_cenario(str(arquivo))


def test_injecao_particao_bidirecional_e_cura(cluster_falhas):
    nodes = cluster_falhas
    addrs = {n.id: f"127.0.0.1:{n.port}" for n in nodes}
    injetor = FaultInjector(node_addrs=addrs, modo_docker=False)

    # Antes da partição: todos se comunicam normalmente
    assert nodes[0].net.send(3, "ping") is not None
    assert nodes[2].net.send(1, "ping") is not None

    # Injeta partição entre [1, 2] e 3
    injetor.executar_evento({
        "time_s": 0.0,
        "action": "partition",
        "nodes": [1, 2],
        "target": 3,
    })

    # Nós 1 e 2 não falam com o 3
    assert nodes[0].net.send(3, "ping") is None
    assert nodes[1].net.send(3, "ping") is None
    # Nó 3 também não fala com o 1 (bidirecional)
    assert nodes[2].net.send(1, "ping") is None
    # Mas nós 1 e 2 continuam falando entre si
    assert nodes[0].net.send(2, "ping") is not None

    # Cura a partição
    injetor.executar_evento({
        "time_s": 0.0,
        "action": "heal",
        "nodes": [1, 2],
        "target": 3,
    })

    # Comunicação com o nó 3 restabelecida
    assert nodes[0].net.send(3, "ping") is not None
    assert nodes[2].net.send(1, "ping") is not None


def test_injecao_degradacao_e_reset(cluster_falhas):
    nodes = cluster_falhas
    addrs = {n.id: f"127.0.0.1:{n.port}" for n in nodes}
    injetor = FaultInjector(node_addrs=addrs, modo_docker=False)

    # Força 100% de perda no nó 1
    injetor.executar_evento({
        "time_s": 0.0,
        "action": "set_faults",
        "node": 1,
        "drop_prob": 1.0,
    })
    assert nodes[0].net.send(2, "ping") is None

    # Reseta falhas
    injetor.executar_evento({
        "time_s": 0.0,
        "action": "reset_faults",
        "node": 1,
    })
    assert nodes[0].net.send(2, "ping") is not None


def test_injecao_crash_e_recovery_callbacks(cluster_falhas):
    crashed = []
    recovered = []

    injetor = FaultInjector(
        node_addrs={},
        modo_docker=False,
        on_crash=lambda nid: crashed.append(nid),
        on_recover=lambda nid: recovered.append(nid),
    )

    injetor.executar_evento({"time_s": 0.0, "action": "crash", "node": 2})
    injetor.executar_evento({"time_s": 0.0, "action": "recover", "node": 2})

    assert crashed == [2]
    assert recovered == [2]


def test_execucao_cenario_completo_rapido(cluster_falhas):
    nodes = cluster_falhas
    addrs = {n.id: f"127.0.0.1:{n.port}" for n in nodes}
    injetor = FaultInjector(node_addrs=addrs, modo_docker=False)

    cenario_teste = {
        "name": "mini_teste",
        "duration_s": 0.3,
        "events": [
            {"time_s": 0.05, "action": "partition", "nodes": [1], "target": 2},
            {"time_s": 0.15, "action": "heal", "nodes": [1], "target": 2},
        ],
    }

    injetor.run(cenario_teste)

    # Confere se os eventos foram registrados nos logs JSONL do nó 1
    eventos_1 = [e["event"] for e in read_events(nodes[0].log.path)]
    assert "control_received" in eventos_1
    assert "partition_start" in eventos_1
    assert "partition_end" in eventos_1
