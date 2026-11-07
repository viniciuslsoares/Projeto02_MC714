"""Testes do gerador aleatório por enlace: cada peer de destino tem seu próprio fluxo de sorteios."""
import threading

from src import config as cfg
from src.node import Node
from tests.test_infra import make_cluster


def test_perdas_por_enlace_reprodutiveis_com_threads_em_paralelo(tmp_path):
    # Duas threads enviam ao mesmo tempo do nó 1: uma só para o 2, outra só para o 3.
    # Com um gerador por enlace, a ordem entre as threads não muda quais pings se perdem.
    def run(sub):
        nodes = make_cluster(tmp_path / sub, n=3, rpc_timeout=0.05,
                             faults=cfg.NetFaults(drop_prob=0.3))
        perdidos = {2: set(), 3: set()}

        def enviar(peer):
            for i in range(20):
                if nodes[0].net.send(peer, "ping") is None:
                    perdidos[peer].add(i)

        threads = [threading.Thread(target=enviar, args=(peer,)) for peer in (2, 3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for n in nodes:
            n.stop()
        return perdidos

    r1, r2 = run("a"), run("b")
    assert r1 == r2                                  # mesmo enlace + mesma semente => mesmas perdas
    total = len(r1[2]) + len(r1[3])
    assert 0 < total < 40                            # houve perda, mas nem tudo se perdeu


def test_peers_diferentes_recebem_fluxos_diferentes(tmp_path):
    def novo_no(sub):
        return Node(cfg.NodeConfig(1, {}, port=0, seed=7, log_dir=str(tmp_path / sub)), echo_log=False)

    a = novo_no("a")
    para_2 = [a.net.sorteio(2) for _ in range(10)]
    para_3 = [a.net.sorteio(3) for _ in range(10)]
    assert para_2 != para_3                          # cada enlace tem o seu fluxo

    # Mesma semente em outro nó: o fluxo do enlace até o 2 se repete, mesmo com
    # sorteios para o 3 intercalados (o tráfego de um enlace não afeta o outro).
    b = novo_no("b")
    intercalado = []
    for _ in range(10):
        b.net.sorteio(3)
        intercalado.append(b.net.sorteio(2))
    assert intercalado == para_2
    a.stop()
    b.stop()
