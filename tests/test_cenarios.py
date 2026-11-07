"""Testes dos cenários de falha (issue #7): os YAML do repositório são válidos, e um cenário
pequeno com partição, cura, crash e retorno roda com processos reais e grava o CSV."""
import csv
import glob
import json
import os

from experiments.cenarios import COLUNAS, N, executar
from experiments.fault_injector import carregar_cenario


def test_cenarios_do_repositorio_sao_validos():
    caminhos = sorted(glob.glob("scenarios/*.yaml"))
    cenarios = [carregar_cenario(c) for c in caminhos]
    assert len(cenarios) >= 5                                   # 2 da issue #2 + 3 da issue #7
    assert len({c["seed"] for c in cenarios}) == len(cenarios)  # sementes registradas e distintas
    acoes = {"partition", "heal", "crash", "recover", "set_faults", "reset_faults"}
    for c in cenarios:
        for e in c["events"]:
            assert e["action"] in acoes
            nos = e.get("nodes", []) + [e[k] for k in ("node", "target") if k in e]
            assert all(1 <= x <= N for x in nos)                # os 3 nós do compose


def test_cenario_com_processos_reais(tmp_path):
    yaml = tmp_path / "mini.yaml"
    yaml.write_text("""
name: "mini"
duration_s: 9.3
seed: 7
events:
  - {time_s: 0.3, action: "partition", nodes: [1, 2], target: 3}
  - {time_s: 3.3, action: "heal", nodes: [1, 2], target: 3}
  - {time_s: 4.8, action: "crash", node: 3}
  - {time_s: 7.3, action: "recover", node: 3}
""", encoding="utf-8")
    prazo = 0.9 + 0.2 + 2 * 0.3 + 0.5                           # o do verificador
    executar([str(yaml)], repeticoes=1, saida=str(tmp_path / "out"), logs=str(tmp_path / "logs"),
             comando="pytest", aquecimento_s=0.5, hb_intervalo=0.2, hb_timeout=0.9, rpc_timeout=0.3)

    assert sorted(os.listdir(tmp_path / "out")) == ["cenarios.csv", "cenarios.csv.meta.json"]
    with open(tmp_path / "out" / "cenarios.csv", encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    assert len(linhas) == 1 and list(linhas[0]) == list(COLUNAS)
    l = linhas[0]
    assert (l["cenario"], l["seed"], l["eventos"]) == ("mini", "7", "partition:3;heal:3;crash:3;recover:3")
    particao, cura, crash, volta = l["reconvergencia_s"].split(";")
    assert particao == ""                                       # dois líderes até a cura
    assert all(0 < float(x) <= prazo for x in (cura, crash, volta))
    assert l["lideres_max"] == "2" and float(l["dois_lideres_s"]) > 0
    # o lado sem o 3 elege o 2 com o 3 vivo (split-brain); a convergência vale depois da cura
    avisos = l["avisos_verificador"].split(";")
    assert "bully.acordo" in avisos and "bully.convergencia" not in avisos

    with open(tmp_path / "out" / "cenarios.csv.meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["completa"] and meta["comando"] == "pytest" and meta["parametros"]["n"] == 3
    assert [c["name"] for c in meta["cenarios"].values()] == ["mini"]
    assert {"git_commit", "git_alteracoes_locais", "inicio_utc", "duracao_total_s", "ambiente"} <= set(meta)
