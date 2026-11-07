"""Testes do verificador de logs (issue #5): violações deliberadas de cada propriedade
são detectadas, e execuções corretas (sintética e real, com crash) não geram aviso."""
import json
import time

import pytest

from analysis.log_verifier import ler_vidas, lideres_ao_mesmo_tempo, main, verificar
from src import config as cfg
from src.bully import Bully
from src.detector import HeartbeatDetector
from src.node import Node
from tests.test_bully import INTERVALO, LIMITE, QUIETO, TIMEOUT, TIMEOUT_HB, espera, liga_bully, lideres
from tests.test_infra import make_cluster


def ev(no, ts, event, **data):
    return {"ts": ts, "node": no, "clock": 0, "event": event, "data": data}


def sem_falhas(fim=3.0):
    # 3 nós, heartbeats a cada 0,1 s entre todos, e todos com o líder 3 desde 0,1 s
    logs = {}
    for i in (1, 2, 3):
        outros = [j for j in (1, 2, 3) if j != i]
        logs[i] = [ev(i, 0.0, "node_start"),
                   ev(i, 0.0, "detector_start", intervalo=0.1, timeout=0.5, peers=outros),
                   ev(i, 0.0, "bully_start", timeout=0.3),
                   ev(i, 0.1, "leader", lider=3)]
        logs[i] += [ev(i, k / 10, "recv", peer=j, msg="heartbeat")
                    for k in range(1, round(fim * 10) + 1) for j in outros]
    logs[3].append(ev(3, 0.05, "election_start", motivo="sem_lider", maiores=[]))
    return logs


def sem_heartbeat(logs, observador, peer, de, ate):
    # tira os heartbeats de `peer` recebidos por `observador` em (de, ate]
    logs[observador] = [e for e in logs[observador] if not (
        e["event"] == "recv" and e["data"]["peer"] == peer and de < e["ts"] <= ate)]


def crash_do_3(logs, eleger_2):
    # o nó 3 morre em 1,0 s como num SIGKILL: sem node_stop e com a última linha cortada.
    # 1 e 2 suspeitam dele em 1,6 s; com eleger_2, o 2 se elege e o 1 aceita em 1,7 s
    logs[3] = [e for e in logs[3] if e["ts"] <= 1.0] + [{"ts": 1.0, "cortada": True}]
    for i in (1, 2):
        sem_heartbeat(logs, i, 3, 1.0, 99)
        logs[i].append(ev(i, 1.6, "suspect", peer=3))
    if eleger_2:
        logs[2].append(ev(2, 1.65, "election_start", motivo="lider_suspeito", maiores=[3]))
        logs[1].append(ev(1, 1.7, "leader", lider=2))
        logs[2].append(ev(2, 1.7, "leader", lider=2))


def volta_o_3(logs, t):
    # o nó 3 volta em t (nova vida no mesmo arquivo) e se elege; 1 e 2 o restauram e aceitam
    logs[3] += [ev(3, t, "node_start"),
                ev(3, t, "detector_start", intervalo=0.1, timeout=0.5, peers=[1, 2]),
                ev(3, t, "bully_start", timeout=0.3),
                ev(3, t + 0.01, "election_start", motivo="sem_lider", maiores=[]),
                ev(3, t + 0.02, "leader", lider=3)]
    batidas = [k / 10 for k in range(1, 31) if k / 10 > t]
    logs[3] += [ev(3, h, "recv", peer=j, msg="heartbeat") for h in batidas for j in (1, 2)]
    for i in (1, 2):
        logs[i] += [ev(i, h, "recv", peer=3, msg="heartbeat") for h in batidas]
        logs[i] += [ev(i, batidas[0] + 0.05, "restore", peer=3), ev(i, t + 0.03, "leader", lider=3)]


def crash_e_volta_no_meio_da_eleicao(logs):
    # o 3 volta em 1,68 s, durante a eleição do 2 (1,65 a 1,7 s): a decisão pelo 2 não viola
    # o acordo, porque o 3 não estava vivo durante toda essa eleição
    crash_do_3(logs, eleger_2=True)
    volta_o_3(logs, 1.68)


def crash_gracioso_do_3(logs):
    # como crash_do_3, mas por stop(): node_stop em 1,0 s, e heartbeats que ainda chegam
    # durante o shutdown do servidor; o que vem depois do node_stop não conta
    crash_do_3(logs, eleger_2=True)
    logs[3] = [e for e in logs[3] if not e.get("cortada")] + [ev(3, 1.0, "node_stop")]
    logs[3] += [ev(3, k / 10, "recv", peer=j, msg="heartbeat") for k in range(11, 19) for j in (1, 2)]


def nos_sobem_quase_juntos(logs):
    # processos locais podem subir a menos de 1 µs um do outro (menos que o EPS do verificador)
    for i, eventos in logs.items():
        for e in eventos:
            if e["ts"] == 0.0:
                e["ts"] = (i - 1) * 4e-7


def logs_antigos_sem_peers(logs):
    for eventos in logs.values():
        for e in eventos:
            e["data"].pop("peers", None)


def violacao_suspeita_cedo(logs):
    logs[1] += [ev(1, 1.0, "suspect", peer=2), ev(1, 1.15, "restore", peer=2)]


def violacao_restore_sem_suspect(logs):
    logs[1].append(ev(1, 1.0, "restore", peer=2))


def violacao_silencio_sem_suspeita(logs):
    sem_heartbeat(logs, 1, 3, 1.0, 99)


def violacao_suspeita_tarde(logs):
    crash_do_3(logs, eleger_2=True)
    logs[1] = [e for e in logs[1] if e["event"] != "suspect"]
    logs[1].append(ev(1, 2.5, "suspect", peer=3))      # 1,5 s depois do último heartbeat


def violacao_suspeita_nunca_retirada(logs):
    sem_heartbeat(logs, 1, 2, 0.4, 1.0)
    logs[1].append(ev(1, 1.0, "suspect", peer=2))      # justa: 0,6 s sem heartbeat


def violacao_lider_menor(logs):
    logs[1].append(ev(1, 1.0, "leader", lider=2))       # o nó 3 está vivo...
    logs[1].append(ev(1, 1.05, "leader", lider=3))      # ...e logo se anuncia de novo


def violacao_nao_converge(logs):
    crash_do_3(logs, eleger_2=False)                    # 1 e 2 ficam com o líder morto
    # como no fim de um cenário do injetor: o reset das falhas não cancela a cobrança
    logs[1].append(ev(1, 2.0, "faults_reset"))


def violacao_converge_e_diverge(logs):
    # sem nenhuma mudança, o nó 2 passa a se achar líder com o nó 3 vivo e fica assim
    logs[2].append(ev(2, 1.0, "election_start", motivo="teste", maiores=[3]))
    logs[2].append(ev(2, 1.0, "leader", lider=2))


def particao_queda_e_cura(logs):
    # falhas combinadas (issue #7): o nó 3 fica isolado de 1 e 2 em 1,0 s, e 1 e 2 elegem o 2
    # em 1,7 s (dois líderes: 2 e 3). O nó 1 cai em 2,0 s, durante a partição. A cura em 4,0 s
    # junta 2 e 3, e o 2 aceita o 3 em 4,2 s
    for i, peers in ((1, [3]), (2, [3]), (3, [1, 2])):
        logs[i] += [ev(i, 1.0, "partition_start", peer=p) for p in peers]
        for p in peers:
            sem_heartbeat(logs, i, p, 1.0, 4.0)
            logs[i].append(ev(i, 1.6, "suspect", peer=p))
    logs[2].append(ev(2, 1.65, "election_start", motivo="lider_suspeito", maiores=[3]))
    logs[1].append(ev(1, 1.7, "leader", lider=2))
    logs[2].append(ev(2, 1.7, "leader", lider=2))
    logs[1] = [e for e in logs[1] if e["ts"] <= 2.0]                 # SIGKILL do nó 1
    for i in (2, 3):
        sem_heartbeat(logs, i, 1, 2.0, 99)
    logs[2].append(ev(2, 2.6, "suspect", peer=1))                     # o 3 já suspeitava do 1
    for i, peers in ((2, [3]), (3, [1, 2])):
        logs[i] += [ev(i, 4.0, "partition_end", peer=p) for p in peers]
    logs[2] += [ev(2, 4.15, "restore", peer=3),
                ev(2, 4.15, "election_start", motivo="no_maior_vivo", maiores=[3]),
                ev(2, 4.2, "leader", lider=3)]
    logs[3].append(ev(3, 4.15, "restore", peer=2))


def gravar(pasta, logs):
    for i, eventos in logs.items():
        with open(pasta / f"node{i}.jsonl", "w", encoding="utf-8") as f:
            for e in sorted(eventos, key=lambda e: e["ts"]):
                f.write(f'{{"ts": {e["ts"]}, "node"' if e.get("cortada") else json.dumps(e))
                f.write("\n")


def avisos(tmp_path, logs):
    gravar(tmp_path, logs)
    _, props = verificar(str(tmp_path))
    return {nome for ok, nome, _ in props if not ok}


@pytest.mark.parametrize("violar, propriedade", [
    (violacao_suspeita_cedo, "detector.suspeita_apos_timeout"),
    (violacao_restore_sem_suspect, "detector.alternancia"),
    (violacao_silencio_sem_suspeita, "detector.completude"),
    (violacao_suspeita_tarde, "detector.completude"),
    (violacao_suspeita_nunca_retirada, "detector.restauracao"),
    (violacao_lider_menor, "bully.acordo"),
    (violacao_nao_converge, {"bully.convergencia"}),
    (violacao_converge_e_diverge, {"bully.acordo", "bully.convergencia"}),
])
def test_violacao_deliberada_e_detectada(tmp_path, violar, propriedade):
    logs = sem_falhas()
    violar(logs)
    esperado = propriedade if isinstance(propriedade, set) else {propriedade}
    assert avisos(tmp_path, logs) == esperado           # só a(s) propriedade(s) violada(s)


@pytest.mark.parametrize("cenario", [
    lambda logs: None,
    lambda logs: crash_do_3(logs, eleger_2=True),       # crash tratado como o modelo prevê
    crash_e_volta_no_meio_da_eleicao,
    crash_gracioso_do_3,
    nos_sobem_quase_juntos,
    logs_antigos_sem_peers,
])
def test_execucao_correta_sem_avisos(tmp_path, cenario):
    logs = sem_falhas()
    cenario(logs)
    assert avisos(tmp_path, logs) == set()


def test_falhas_combinadas_split_brain_e_convergencia_depois_da_cura(tmp_path):
    logs = sem_falhas(fim=6.0)
    particao_queda_e_cura(logs)
    # o acordo é violado (cada lado tem o seu líder); a queda do 1 durante a partição não abre
    # cobrança de convergência, e a cura abre uma, que é cumprida
    assert avisos(tmp_path, logs) == {"bully.acordo"}
    _, props = verificar(str(tmp_path))
    texto = dict((n, t) for _, n, t in props)["bully.convergencia"]
    assert "3 mudanças conferidas" in texto and "1 não conferidas" in texto   # a queda do 1
    assert lideres_ao_mesmo_tempo(ler_vidas(str(tmp_path)), 0.0, 6.0) == (2, pytest.approx(2.5))


def test_relatorio(tmp_path):
    avisos(tmp_path, sem_falhas())
    saida = tmp_path / "verifications.txt"
    main([str(tmp_path), "--saida", str(saida)])
    linhas = saida.read_text(encoding="utf-8").splitlines()
    assert sum(l.startswith("[OK]") for l in linhas) == 6
    assert not any(l.startswith("[AVISO]") for l in linhas)


def test_execucao_real_com_crash_e_retorno_sem_avisos(tmp_path):
    nodes = make_cluster(tmp_path, n=3)
    liga_bully(nodes)
    assert espera(lambda: lideres(nodes) == {1: 3, 2: 3, 3: 3})
    vivos, ligados = nodes[:2], list(nodes)
    try:
        porta = nodes[2].port
        nodes[2].stop()                                 # crash do líder
        ligados.remove(nodes[2])
        assert espera(lambda: lideres(vivos) == {1: 2, 2: 2}, LIMITE)
        time.sleep(QUIETO)

        # o nó 3 volta (processo novo, mesma porta e mesmo arquivo de log)
        novo = Node(cfg.NodeConfig(3, {n.id: f"127.0.0.1:{n.port}" for n in vivos}, port=porta,
                                   seed=3, log_dir=str(tmp_path)), echo_log=False)
        novo.detector = HeartbeatDetector(novo, INTERVALO, TIMEOUT_HB)
        novo.bully = Bully(novo, novo.detector, TIMEOUT)
        novo.start()
        ligados.append(novo)
        assert espera(lambda: lideres(ligados) == {1: 3, 2: 3, 3: 3})
        time.sleep(QUIETO)
    finally:
        for n in ligados:
            n.stop()
    _, props = verificar(str(tmp_path))
    assert [(nome, texto) for ok, nome, texto in props if not ok] == []
