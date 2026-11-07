"""Testes de exceção dentro do handler de um algoritmo: deve virar resposta de erro e aparecer no log."""
import pytest

from src.eventlog import read_events
from tests.test_infra import make_cluster


@pytest.fixture
def cluster(tmp_path):
    nodes = make_cluster(tmp_path, n=2)
    yield nodes
    for n in nodes:
        n.stop()


def handler_quebrado(msg):
    raise RuntimeError("bug no algoritmo")


def test_excecao_no_handler_vira_resposta_de_erro_e_log(cluster):
    a, b = cluster
    b.register("quebra", handler_quebrado)
    reply = a.net.send(2, "quebra")
    assert reply is not None and reply.type == "error"
    assert reply.payload["error"] == "handler_exception"
    erros = [e for e in read_events(b.log.path) if e["event"] == "handler_error"]
    assert len(erros) == 1
    assert erros[0]["data"]["error"] == "RuntimeError"
    assert erros[0]["data"]["detail"] == "bug no algoritmo"
    assert erros[0]["data"]["msg"] == "quebra" and erros[0]["data"]["peer"] == 1


def test_no_continua_atendendo_depois_do_erro(cluster):
    a, b = cluster
    b.register("quebra", handler_quebrado)
    a.net.send(2, "quebra")
    reply = a.net.send(2, "ping")
    assert reply is not None and reply.type == "ok" and reply.payload == {"pong": 2}


def test_handler_que_devolve_none_responde_ok_vazio(cluster):
    a, b = cluster
    b.register("nada", lambda msg: None)
    reply = a.net.send(2, "nada")
    assert reply is not None and reply.type == "ok" and reply.payload == {}
