"""Testes do relógio de Lamport no crash-recovery: o nó reinicia e o relógio não volta para trás."""
import json

from src import config as cfg
from src.eventlog import read_events
from src.node import Node


def novo_no(tmp_path):
    c = cfg.NodeConfig(node_id=1, peers={}, port=0, log_dir=str(tmp_path))
    return Node(c, echo_log=False)


def eventos_validos(path):
    # como read_events, mas pulando linhas malformadas
    eventos = []
    with open(path, encoding="utf-8") as f:
        for linha in f:
            try:
                eventos.append(json.loads(linha))
            except ValueError:
                pass
    return eventos


def test_relogio_continua_depois_do_reinicio(tmp_path):
    a = novo_no(tmp_path)
    a.start()
    for i in range(10):
        a.log.emit("trabalho", i=i)
    a.stop()
    antes = read_events(a.log.path)
    ultimo_clock = antes[-1]["clock"]
    assert ultimo_clock > 10

    # "reinício": novo processo com o mesmo id e o mesmo log_dir
    b = novo_no(tmp_path)
    b.start()
    b.stop()
    depois = read_events(b.log.path)[len(antes):]

    assert depois[0]["event"] == "node_start"
    assert depois[0]["clock"] > ultimo_clock
    assert antes[0]["data"]["incarnation"] == 1
    assert depois[0]["data"]["incarnation"] == 2


def test_linha_truncada_nao_impede_inicializacao(tmp_path):
    a = novo_no(tmp_path)
    a.start()
    a.stop()
    ultimo_clock = read_events(a.log.path)[-1]["clock"]
    # simula SIGKILL no meio da escrita: última linha cortada, sem "\n"
    with open(a.log.path, "a", encoding="utf-8") as f:
        f.write('{"clock": 999, "event": "trab')

    b = novo_no(tmp_path)
    b.start()
    b.stop()

    inicios = [e for e in eventos_validos(b.log.path) if e["event"] == "node_start"]
    assert len(inicios) == 2
    assert inicios[1]["data"]["incarnation"] == 2
    assert inicios[1]["clock"] > ultimo_clock
