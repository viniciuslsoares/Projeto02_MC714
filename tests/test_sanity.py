"""Teste de sanidade ponta a ponta (issue #5): sobe 3 nós com detector e Bully, elege
o líder e confere os logs com o verificador. Roda em poucos segundos."""
import time

from analysis.log_verifier import verificar
from tests.test_bully import INTERVALO, espera, liga_bully, lideres
from tests.test_infra import make_cluster


def test_sanity(tmp_path):
    nodes = make_cluster(tmp_path, n=3)
    liga_bully(nodes)
    ok = espera(lambda: lideres(nodes) == {1: 3, 2: 3, 3: 3})
    time.sleep(10 * INTERVALO)     # alguns heartbeats depois da eleição
    for n in nodes:
        n.stop()
    assert ok
    _, props = verificar(str(tmp_path))
    assert [(nome, texto) for ok, nome, texto in props if not ok] == []
    textos = {nome: texto for _, nome, texto in props}
    assert int(textos["bully.convergencia"].split()[0]) >= 1    # a convergência foi conferida
