# Serviço de tarefas (issue #8): roda os cenários de scenarios/integracao/ com o cliente gerando
# carga e mede a aplicação. Reaproveita a execução dos cenários da #7 (3 nós, um processo por
# nó em 127.0.0.1, FaultInjector com crash por SIGKILL e volta com um processo novo).
#
# Uso (da raiz do repositório):
#   python -m experiments.integracao                     # os 2 cenários, 5 repetições (~6 min)
#   python -m experiments.integracao scenarios/integracao/quedas.yaml --repeticoes 1 --saida /tmp/teste
# (sem --saida, sobrescreve os CSVs versionados em results/experimentos/)
# O run_all também roda a integração. Os logs de cada execução, inclusive o do cliente
# (cliente.jsonl), ficam em results/raw/integracao/<cenário>_seed<semente>/ (fora do git).
#
# Saída: integracao.csv (uma linha por execução), integracao_tarefas.csv (uma linha por tarefa,
# para percentis e figuras) e integracao.csv.meta.json. Métricas e resultados: docs/integracao.md.
import argparse
import glob
import json
import os
import signal
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone

from analysis.log_verifier import ler_vidas, verificar
from experiments.cenarios import AQUECIMENTO_S, N, PARAMS, rodar_cenario, rotulo
from experiments.fault_injector import carregar_cenario
from experiments.grid import RAIZ, ambiente, escrever_atomico, escrever_csv, proveniencia
from src.cliente import Cliente
from src.eventlog import read_events

TAXA = 20.0         # tarefas por segundo (carga aberta)
TAREFA_MS = 100     # duração de cada tarefa no worker

COLUNAS = {
    "cenario": "nome do cenário (campo name do YAML)",
    "repeticao": "número da repetição (1, 2, ...)",
    "seed": "SEED dos nós: a do YAML + repetição - 1",
    "n": "número de nós (o líder e n - 1 workers)",
    "taxa": "tarefas enviadas por segundo (carga aberta)",
    "tarefa_ms": "duração de cada tarefa no worker (ms)",
    "hb_intervalo": "HB_INTERVAL (s)",
    "hb_timeout": "HB_TIMEOUT (s)",
    "rpc_timeout": "RPC_TIMEOUT (s), também a folga da espera do líder pelo worker",
    "duracao_s": "duração total da execução, da subida dos processos ao fim (s)",
    "eventos": "eventos do cenário na ordem, como ação:nó, separados por ';'",
    "tarefas": "tarefas enviadas pelo cliente",
    "confirmadas": "tarefas confirmadas ao cliente como feitas",
    "vazao_s": "tarefas confirmadas por segundo, do primeiro envio à última confirmação",
    "lat_p50_ms": "latência (do primeiro envio da tarefa à confirmação), percentil 50 (ms)",
    "lat_p95_ms": "latência, percentil 95 (ms)",
    "lat_p99_ms": "latência, percentil 99 (ms)",
    "lat_max_ms": "latência máxima (ms)",
    "maior_pausa_s": "maior intervalo entre duas confirmações seguidas (s): a interrupção do serviço",
    "reatribuidas": "tarefas com mais de um task_assign: reatribuídas pelo líder ou reenviadas pelo cliente a outro líder",
    "executadas_2x": "tarefas executadas mais de uma vez (mais de um task_end)",
    "avisos_verificador": "propriedades com [AVISO] no analysis.log_verifier, separadas por ';'",
}

COLUNAS_TAREFAS = {
    "cenario": "nome do cenário",
    "repeticao": "número da repetição",
    "id": "id da tarefa (a k-ésima enviada)",
    "envio_s": "instante do primeiro envio, desde o início do cenário (s)",
    "latencia_s": "do primeiro envio à confirmação (s); vazio = não confirmada",
    "pedidos": "pedidos TASK do cliente até a confirmação (inclui os redirecionados)",
    "lider": "nó que confirmou a tarefa",
    "worker": "worker que a executou na confirmação",
    "atribuicoes": "task_assign da tarefa, em todos os líderes",
    "execucoes": "task_end da tarefa, em todos os workers",
}


def percentil(xs, q):
    # interpolação linear entre as amostras (o "linear" do numpy)
    return xs[0] if len(xs) == 1 else statistics.quantiles(xs, n=100, method="inclusive")[q - 1]


def medir(pasta, t_ini):
    cliente = read_events(os.path.join(pasta, "cliente.jsonl"))
    vidas = ler_vidas(pasta)
    envios = {e["data"]["id"]: e["ts"] for e in cliente if e["event"] == "tarefa_enviada"}
    confirmadas = {e["data"]["id"]: e for e in cliente if e["event"] == "tarefa_confirmada"}
    atribuicoes = Counter(e["data"]["task"] for v in vidas for e in v.do_tipo("task_assign"))
    execucoes = Counter(e["data"]["task"] for v in vidas for e in v.do_tipo("task_end"))

    lat = sorted(e["data"]["latencia_s"] * 1000 for e in confirmadas.values())
    ts = sorted(e["ts"] for e in confirmadas.values())
    _, props = verificar(pasta)
    linha = {
        "tarefas": len(envios),
        "confirmadas": len(confirmadas),
        "vazao_s": round(len(ts) / (ts[-1] - min(envios.values())), 2) if ts else 0,
        **{f"lat_p{q}_ms": round(percentil(lat, q), 1) if lat else None for q in (50, 95, 99)},
        "lat_max_ms": round(lat[-1], 1) if lat else None,
        "maior_pausa_s": round(max(b - a for a, b in zip(ts, ts[1:])), 3) if len(ts) > 1 else None,
        "reatribuidas": sum(1 for k in envios if atribuicoes[k] > 1),
        "executadas_2x": sum(1 for k in envios if execucoes[k] > 1),
        "avisos_verificador": ";".join(nome for ok, nome, _ in props if not ok),
    }
    tarefas = []
    for k in sorted(envios):
        c = confirmadas.get(k, {"data": {}})["data"]
        tarefas.append({"id": k, "envio_s": round(envios[k] - t_ini, 3), "latencia_s": c.get("latencia_s"),
                        "pedidos": c.get("pedidos"), "lider": c.get("lider"), "worker": c.get("worker"),
                        "atribuicoes": atribuicoes[k], "execucoes": execucoes[k]})
    return linha, tarefas


def executar(caminhos=None, repeticoes=5, saida="results/experimentos", logs="results/raw/integracao",
             comando=None, aquecimento_s=AQUECIMENTO_S, taxa=TAXA, tarefa_ms=TAREFA_MS, **params):
    caminhos = caminhos or sorted(glob.glob(os.path.join(RAIZ, "scenarios", "integracao", "*.yaml")))
    cenarios = [carregar_cenario(c) for c in caminhos]
    params = {**PARAMS, **params}
    inicio = datetime.now(timezone.utc)
    caminho = os.path.join(saida, "integracao.csv")
    linhas, tarefas = [], []
    try:
        # repetições por fora, como nas grades da #6 e nos cenários da #7
        for r in range(1, repeticoes + 1):
            for cen in cenarios:
                seed = cen.get("seed", 0) + r - 1
                pasta = os.path.abspath(os.path.join(logs, f"{cen['name']}_seed{seed}"))

                def carga(portas):
                    Cliente({i: f"127.0.0.1:{p}" for i, p in portas.items()}, pasta, tarefa_ms,
                            params["rpc_timeout"]).rodar(taxa, cen["duration_s"])

                t0 = time.time()
                t_ini, _, t_fim = rodar_cenario(cen, seed, pasta, aquecimento_s, junto=carga, **params)
                medidas, por_tarefa = medir(pasta, t_ini)
                base = {"cenario": cen["name"], "repeticao": r}
                linha = {**base, "seed": seed, "n": N, "taxa": taxa, "tarefa_ms": tarefa_ms, **params,
                         "duracao_s": round(time.time() - t0, 2),
                         "eventos": ";".join(rotulo(e) for e in cen["events"]), **medidas}
                linhas.append(linha)
                tarefas += [{**base, **t} for t in por_tarefa]
                print(f"[integracao] {cen['name']} rep {r}/{repeticoes}: {linha['confirmadas']}/"
                      f"{linha['tarefas']} confirmadas, p50/p95/p99 {linha['lat_p50_ms']}/"
                      f"{linha['lat_p95_ms']}/{linha['lat_p99_ms']} ms, pausa {linha['maior_pausa_s']} s, "
                      f"{linha['reatribuidas']} reatribuídas, {linha['executadas_2x']} executadas 2x, "
                      f"avisos: {linha['avisos_verificador'] or '-'}", flush=True)
    finally:
        escrever_csv(caminho, COLUNAS, linhas)
        escrever_csv(os.path.join(saida, "integracao_tarefas.csv"), COLUNAS_TAREFAS, tarefas)
        meta = {
            "completa": len(linhas) == repeticoes * len(cenarios),
            **proveniencia(inicio, comando),
            "repeticoes": repeticoes,
            "parametros": {"n": N, "taxa": taxa, "tarefa_ms": tarefa_ms, **params},
            "execucao": {"aquecimento_s": aquecimento_s, "crash": "SIGKILL do processo",
                         "recover": "processo novo, mesma porta e mesmo log",
                         "cliente": "src/cliente.py no processo do executor, carga aberta durante duration_s"},
            "cenarios": {os.path.relpath(c, RAIZ): cen for c, cen in zip(caminhos, cenarios)},
            "ambiente": ambiente(),
            "colunas": COLUNAS,
            "colunas_tarefas": COLUNAS_TAREFAS,
        }
        escrever_atomico(caminho + ".meta.json", json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    return linhas


def main(argv=None):
    p = argparse.ArgumentParser(description="Roda o serviço de tarefas com carga e grava os CSVs brutos")
    p.add_argument("cenarios", nargs="*", help="arquivos YAML (padrão: scenarios/integracao/*.yaml)")
    p.add_argument("--repeticoes", type=int, default=5)
    p.add_argument("--saida", default="results/experimentos")
    args = p.parse_args(argv)
    # SIGTERM (kill) vira SystemExit: os nós são parados e o que já rodou é gravado
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    executar([os.path.abspath(c) for c in args.cenarios] or None, args.repeticoes, args.saida,
             comando=" ".join(["python -m experiments.integracao"] + (sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    main()
