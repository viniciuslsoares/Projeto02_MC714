"""Testes da perda de mensagens: o emissor só percebe a falha pelo timeout, e a resposta também pode se perder."""
import time

from src import config as cfg
from src.eventlog import read_events
from tests.test_infra import make_cluster


def test_particao_so_e_percebida_pelo_timeout(tmp_path):
    nodes = make_cluster(tmp_path, n=2, rpc_timeout=0.2)
    a = nodes[0]
    a.net.block(2)
    t0 = time.monotonic()
    assert a.net.send(2, "ping") is None
    assert time.monotonic() - t0 >= 0.2       # não volta na hora: espera o timeout
    assert any(e["event"] == "drop" and e["data"]["reason"] == "partition" for e in read_events(a.log.path))
    for n in nodes:
        n.stop()


def test_perda_da_resposta(tmp_path, monkeypatch):
    nodes = make_cluster(tmp_path, n=2, faults=cfg.NetFaults(drop_prob=0.5), rpc_timeout=0.1)
    a, b = nodes[0], nodes[1]
    # 1º sorteio (requisição) = 0.9 -> passa; 2º sorteio (resposta) = 0.1 -> perde
    valores = iter([0.9, 0.1])
    monkeypatch.setattr(a.net, "sorteio", lambda *args: next(valores))

    t0 = time.monotonic()
    assert a.net.send(2, "ping") is None
    assert time.monotonic() - t0 >= 0.1       # o emissor esperou o timeout

    # o receptor recebeu e processou a mensagem...
    assert any(e["event"] == "recv" and e["data"]["msg"] == "ping" for e in read_events(b.log.path))
    # ...mas a resposta se perdeu no caminho de volta
    eventos_a = read_events(a.log.path)
    assert any(e["event"] == "drop" and e["data"]["reason"] == "reply_loss" for e in eventos_a)
    assert not any(e["event"] == "reply" for e in eventos_a)
    for n in nodes:
        n.stop()
