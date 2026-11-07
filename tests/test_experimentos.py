"""Testes dos experimentos (issue #6): as métricas saem certas de um log sintético, e uma
grade pequena com processos reais grava o CSV e os metadados."""
import csv
import json
import os

import pytest

from experiments.grid import COLUNAS, GridConfig, execute_grid, medir
from tests.test_log_verifier import crash_do_3, ev, gravar, sem_falhas


def test_medir_com_log_sintetico(tmp_path):
    logs = sem_falhas()                     # 3 nós, heartbeats a cada 0,1 s, líder 3
    crash_do_3(logs, eleger_2=True)         # crash em 1,0; 1 e 2 suspeitam em 1,6; líder 2 em 1,7
    for i in (1, 2, 3):
        ultimo = 10 if i == 3 else 30
        logs[i] += [ev(i, k / 10, "send", peer=j, msg="heartbeat", dup=False)
                    for k in range(1, ultimo + 1) for j in (1, 2, 3) if j != i]
    logs[2].append(ev(2, 1.7, "election_end", lider=2, msgs={"ELECTION": 1, "OK": 0, "COORDINATOR": 2}))
    logs[1] += [ev(1, 2.0, "suspect", peer=2), ev(1, 2.1, "restore", peer=2)]   # falso positivo
    gravar(tmp_path, logs)

    m = medir(str(tmp_path), 3, t_ini=0.5, t_crash=1.0, t_fim=3.0)

    assert m["deteccao_s"] == "0.600;0.600" and m["deteccao_max_s"] == 0.6
    assert m["convergencia_s"] == 0.7 and m["eleicao_s"] == 0.1
    assert (m["eleicoes"], m["msgs_election"], m["msgs_ok"], m["msgs_coordinator"], m["msgs_bully"]) == (1, 1, 0, 2, 3)
    # 1 falso positivo em 3 * 2 pares * 0,5 s antes do crash + 2 * 1 pares * 2 s depois = 7 pares * s
    assert m["falsos_positivos"] == 1 and m["fp_por_par_min"] == pytest.approx(60 / 7, abs=1e-4)
    assert m["hb_msgs_por_s"] == 60.0       # n(n - 1) / intervalo = 6 / 0,1
    assert m["avisos_verificador"] == "detector.suspeita_apos_timeout"   # o falso positivo


def test_grade_com_processos_reais(tmp_path):
    cfg = GridConfig("teste", "n", [3], base={"hb_intervalo": 0.2, "hb_timeout": 0.9, "rpc_timeout": 0.3},
                     repeticoes=1, aquecimento_s=0.5, antes_s=1.0, depois_s=3.0)
    r = execute_grid(cfg, saida=str(tmp_path / "out"), logs=str(tmp_path / "logs"), comando="pytest")

    assert sorted(os.listdir(tmp_path / "out")) == ["teste.csv", "teste.csv.meta.json"]   # sem .tmp
    with open(r.csv, encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    assert len(linhas) == 1 and list(linhas[0]) == list(COLUNAS)
    l = linhas[0]
    assert (l["n"], l["seed"], l["hb_intervalo"]) == ("3", "1", "0.2")
    deteccoes = [float(d) for d in l["deteccao_s"].split(";")]
    assert len(deteccoes) == 2 and all(0 < d <= 0.9 + 0.2 + 0.5 for d in deteccoes)
    assert 0 < float(l["convergencia_s"]) <= 0.9 + 0.2 + 2 * 0.3 + 0.5
    assert int(l["msgs_election"]) >= 1 and int(l["msgs_coordinator"]) >= 2
    assert l["falsos_positivos"] == "0" and l["avisos_verificador"] == ""

    with open(r.meta, encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["comando"] == "pytest" and meta["config"]["niveis"] == [3]
    assert {"git_commit", "git_alteracoes_locais", "inicio_utc", "duracao_total_s", "ensaio"} <= set(meta)
    assert {"so", "cpu", "cpus", "python"} <= set(meta["ambiente"])
