"""Testes do serviço de tarefas (issue #8): handlers do despachante, propriedades da aplicação no
verificador de logs e uma execução com processos reais, carga e quedas de worker e de líder."""
import csv
import json
import os
import time

from analysis.log_verifier import verificar
from experiments.integracao import COLUNAS, COLUNAS_TAREFAS, executar
from src.eventlog import read_events
from src.models import Message
from src.tarefas import Despachante
from tests.test_bully import no_isolado
from tests.test_log_verifier import avisos, crash_do_3, ev, sem_falhas


# --- unitários (sem threads de fundo) ---

def test_despachante(tmp_path):
    # nó 2 com peers 1 e 3 que não existem: um EXEC a eles é recusado na hora
    node, b = no_isolado(tmp_path)
    d = Despachante(node, b.detector, b, timeout=0.3)
    tarefa = {"id": 7, "ms": 0, "pedido": 1}

    b.lider = 3     # não é o líder: não despacha e diz quem é
    assert node.handle(Message("TASK", 0, 0, tarefa)).payload == {"status": "nao_sou_lider", "lider": 3}
    # worker: só executa para o próprio líder
    assert node.handle(Message("EXEC", 1, 0, tarefa)).payload == {"feita": False}
    assert node.handle(Message("EXEC", 3, 0, tarefa)).payload == {"feita": True}

    b.lider = 2     # é o líder, sem nenhum worker vivo para o detector
    assert node.handle(Message("TASK", 0, 0, tarefa)).payload == {"status": "falhou"}
    # os dois workers vivos para o detector, mas caídos: tenta cada um uma vez e desiste
    b.detector.ultimo = {1: time.monotonic(), 3: time.monotonic()}
    assert node.handle(Message("TASK", 0, 0, {**tarefa, "pedido": 2})).payload == {"status": "falhou"}
    node.log.close()

    eventos = read_events(node.log.path)
    assert sorted(e["data"]["worker"] for e in eventos if e["event"] == "task_assign") == [1, 3]
    assert [e["data"]["lider"] for e in eventos if e["event"] == "task_rejected"] == [1]
    assert [e["data"]["lider"] for e in eventos if e["event"] == "task_end"] == [3]
    assert d.proximo == 2       # o rodízio avança uma vez por pedido, não por tentativa


# --- verificador com logs sintéticos ---

def com_tarefas(logs, tmp_path, k=3):
    # o cliente manda k tarefas ao líder 3, que as atribui ao 1 e ao 2 em rodízio
    cliente = []
    for t in range(k):
        x, w = 0.2 + t / 10, 1 + t % 2
        cliente += [ev(0, x, "tarefa_enviada", id=t), ev(0, x + 0.06, "tarefa_confirmada", id=t)]
        logs[3] += [ev(3, x + 0.01, "task_assign", task=t, pedido=1, worker=w),
                    ev(3, x + 0.05, "task_reply", task=t, pedido=1, status="feita", worker=w)]
        logs[w] += [ev(w, x + 0.02, "task_start", task=t, pedido=1, lider=3),
                    ev(w, x + 0.04, "task_end", task=t, pedido=1, lider=3)]
    with open(tmp_path / "cliente.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(e) + "\n" for e in cliente)


def test_verificador_tarefas(tmp_path):
    logs = sem_falhas()
    com_tarefas(logs, tmp_path)
    assert avisos(tmp_path, logs) == set()

    # o líder 3 cai com a tarefa 9 em execução no 1; o 2 é eleito e o cliente reenvia a ele:
    # a tarefa roda duas vezes (pelo menos uma vez), mas nunca com os dois líderes ao mesmo tempo
    logs = sem_falhas()
    com_tarefas(logs, tmp_path)
    logs[3] += [ev(3, 0.9, "task_assign", task=9, pedido=1, worker=1)]
    logs[1] += [ev(1, 0.91, "task_start", task=9, pedido=1, lider=3), ev(1, 1.05, "task_end", task=9, pedido=1, lider=3)]
    crash_do_3(logs, eleger_2=True)
    logs[2] += [ev(2, 1.8, "task_assign", task=9, pedido=5, worker=1),
                ev(2, 1.9, "task_reply", task=9, pedido=5, status="feita", worker=1)]
    logs[1] += [ev(1, 1.81, "task_start", task=9, pedido=5, lider=2), ev(1, 1.89, "task_end", task=9, pedido=5, lider=2)]
    with open(tmp_path / "cliente.jsonl", "a", encoding="utf-8") as f:
        f.writelines(json.dumps(e) + "\n" for e in [ev(0, 0.85, "tarefa_enviada", id=9),
                                                    ev(0, 1.95, "tarefa_confirmada", id=9)])
    assert avisos(tmp_path, logs) == set()
    texto = {n: t for _, n, t in verificar(str(tmp_path))[1]}["tarefas.execucao"]
    assert "4 tarefas enviadas" in texto and "1 executadas mais de uma vez" in texto

    # violações: o 2 também atribui a tarefa 0 enquanto ela está com o 3; a tarefa 1 é confirmada
    # sem ter sido executada; a tarefa 5 nunca é confirmada
    logs = sem_falhas()
    com_tarefas(logs, tmp_path)
    logs[2] += [ev(2, 0.22, "task_assign", task=0, pedido=2, worker=1)]
    logs[2] = [e for e in logs[2] if not (e["event"] == "task_end" and e["data"]["task"] == 1)]
    with open(tmp_path / "cliente.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(ev(0, 2.0, "tarefa_enviada", id=5)) + "\n")
    assert avisos(tmp_path, logs) == {"tarefas.execucao", "tarefas.um_lider"}
    texto = {n: t for _, n, t in verificar(str(tmp_path))[1]}["tarefas.execucao"]
    assert texto.startswith("2 de 4:") and "tarefa 1 confirmada" in texto and "tarefa 5 não foi confirmada" in texto


# --- com processos reais ---

def test_integracao_com_processos_reais(tmp_path):
    yaml = tmp_path / "mini.yaml"
    yaml.write_text("""
name: "mini"
duration_s: 8.0
seed: 9
events:
  - {time_s: 1.0, action: "crash", node: 1}
  - {time_s: 2.5, action: "recover", node: 1}
  - {time_s: 4.0, action: "crash", node: 3}
  - {time_s: 6.0, action: "recover", node: 3}
""", encoding="utf-8")
    executar([str(yaml)], repeticoes=1, saida=str(tmp_path / "out"), logs=str(tmp_path / "logs"),
             comando="pytest", aquecimento_s=0.5, taxa=20.0, tarefa_ms=50,
             hb_intervalo=0.2, hb_timeout=0.9, rpc_timeout=0.3)

    saida = tmp_path / "out"
    assert sorted(os.listdir(saida)) == ["integracao.csv", "integracao.csv.meta.json", "integracao_tarefas.csv"]
    with open(saida / "integracao.csv", encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    assert len(linhas) == 1 and list(linhas[0]) == list(COLUNAS)
    l = linhas[0]
    assert l["tarefas"] == l["confirmadas"] == "160"            # 20 tarefas/s por 8 s, todas feitas
    assert int(l["reatribuidas"]) > 0                            # as do worker caído
    assert 50 <= float(l["lat_p50_ms"]) <= float(l["lat_p99_ms"]) <= float(l["lat_max_ms"])
    assert float(l["maior_pausa_s"]) > 0.5                       # sem líder até a eleição do 2
    assert not any(a.startswith("tarefas.") for a in l["avisos_verificador"].split(";"))

    with open(saida / "integracao_tarefas.csv", encoding="utf-8") as f:
        tarefas = list(csv.DictReader(f))
    assert len(tarefas) == 160 and list(tarefas[0]) == list(COLUNAS_TAREFAS)
    assert {t["lider"] for t in tarefas} == {"2", "3"}           # o 2 assume e o 3 retoma
    assert all(int(t["execucoes"]) >= 1 for t in tarefas)

    with open(saida / "integracao.csv.meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["completa"] and meta["comando"] == "pytest" and meta["parametros"]["taxa"] == 20.0
    assert [c["name"] for c in meta["cenarios"].values()] == ["mini"]
