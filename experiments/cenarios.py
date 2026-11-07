# Cenários de falha (issue #7): recuperação, partição e falhas combinadas, descritos em YAML
# (scenarios/*.yaml) e executados pelo injetor da issue #2, com processos locais como nos
# experimentos da #6.
#
# Uso (da raiz do repositório):
#   python -m experiments.cenarios                                   # todos os cenários, 5 repetições
#   python -m experiments.cenarios scenarios/particao_menor_id.yaml --repeticoes 1 --saida /tmp/teste
# (sem --saida, sobrescreve o CSV versionado em results/experimentos/)
# O run_all também roda os cenários. Os logs de cada execução ficam em
# results/raw/cenarios/<cenário>_seed<semente>/ (fora do git).
#
# Uma execução (rodar_cenario):
#   1. sobe os 3 nós, cada um num processo próprio (python -m src.node) em 127.0.0.1, e
#      espera todos terem o nó 3 como líder e mais `aquecimento_s`;
#   2. o FaultInjector executa os eventos do YAML nos instantes marcados: partição, cura e
#      falhas de rede pelo /control de cada nó; crash (SIGKILL) e recover (um processo novo,
#      na mesma porta e com o mesmo arquivo de log) pelos callbacks daqui;
#   3. para os nós (SIGTERM) e mede a partir dos logs (medir).
#
# Métricas, resultados e o que cada cenário viola: docs/cenarios.md.
import argparse
import glob
import json
import os
import shutil
import signal
import sys
import threading
import time
from datetime import datetime, timezone

from analysis.log_verifier import EPS, com_bully, inicio_convergido, ler_vidas, lideres_ao_mesmo_tempo, verificar
from experiments.fault_injector import FaultInjector, carregar_cenario
from experiments.grid import (PADRAO, RAIZ, ambiente, escrever_atomico, escrever_csv, esperar_lider,
                              iniciar_no, parar, portas_livres, proveniencia)

N = 3   # os cenários usam os 3 nós do compose (IDs 1 a 3), então também rodam com run_scenario.sh
PARAMS = {k: PADRAO[k] for k in ("hb_intervalo", "hb_timeout", "rpc_timeout")}
AQUECIMENTO_S = 2.0

COLUNAS = {
    "cenario": "nome do cenário (campo name do YAML)",
    "repeticao": "número da repetição (1, 2, ...)",
    "seed": "SEED dos nós: a do YAML + repetição - 1 (cada nó usa SEED * 1000 + NODE_ID)",
    "n": "número de nós",
    "hb_intervalo": "HB_INTERVAL (s)",
    "hb_timeout": "HB_TIMEOUT (s)",
    "rpc_timeout": "RPC_TIMEOUT (s)",
    "duracao_s": "duração total da execução, da subida dos processos ao fim (s)",
    "eventos": "eventos do cenário na ordem, como ação:nó (o nó isolado, na partição e na cura), separados por ';'",
    "reconvergencia_s": "para cada evento, tempo até o início do último trecho, antes do próximo evento, em que "
                        "todos os vivos têm o maior vivo como líder (s), separados por ';'; vazio = não "
                        "convergiu antes do próximo evento (ex.: durante uma partição)",
    "lideres_max": "maior número de nós vivos que se acharam líderes ao mesmo tempo, durante o cenário",
    "dois_lideres_s": "tempo com mais de um nó se achando líder, durante o cenário (s)",
    "avisos_verificador": "propriedades com [AVISO] no analysis.log_verifier, separadas por ';'",
}


def rotulo(evento):
    alvo = evento.get("node", evento.get("target", "todos"))
    return f"{evento['action']}:{alvo}"


def rodar_cenario(cenario, seed, pasta, aquecimento_s=AQUECIMENTO_S, limite_s=60.0, junto=None, **params):
    # devolve o início e o fim do cenário e o instante (time.time) de cada evento. `junto`
    # (opcional) roda numa thread durante o cenário, com as portas dos nós: o cliente da #8
    params = {**PARAMS, **params}
    shutil.rmtree(pasta, ignore_errors=True)
    os.makedirs(pasta)
    portas = dict(zip(range(1, N + 1), portas_livres(N)))
    procs = {}

    def subir(i):
        procs[i] = iniciar_no(pasta, i, portas, seed, 0.0, **params)

    def derrubar(i):
        procs[i].kill()                 # SIGKILL: o nó morre sem rodar código de encerramento
        procs[i].wait()

    try:
        for i in portas:
            subir(i)
        esperar_lider(portas, N, limite_s, pasta)
        time.sleep(aquecimento_s)
        injetor = FaultInjector({i: f"127.0.0.1:{p}" for i, p in portas.items()}, modo_docker=False,
                                on_crash=derrubar, on_recover=subir)
        t_ini = time.time()
        carga = threading.Thread(target=junto, args=(portas,), daemon=True) if junto else None
        if carga:
            carga.start()
        injetor.run(cenario)            # no fim, reset_faults em todos os nós
        if carga:
            carga.join()
        t_fim = time.time()
    finally:
        parar(procs)
    return t_ini, injetor.instantes, t_fim


def medir(pasta, eventos, t_ini, instantes, t_fim):
    vidas = ler_vidas(pasta)
    bs = com_bully(vidas)
    # sem node_stop, a vida termina no último evento do log, um pouco antes do SIGKILL; aqui
    # sabemos o instante do crash e estendemos a vida até ele (o nó ainda estava no ar)
    for ev, c in zip(eventos, instantes):
        if ev["action"] == "crash":
            vida = max((v for v in bs if v.no == ev["node"] and v.inicio <= c), key=lambda v: v.inicio)
            vida.fim = max(vida.fim, c)
    reconvergencias = []
    for c, h in zip(instantes, instantes[1:] + [t_fim]):
        t = inicio_convergido(bs, c, max(c, h - EPS))
        reconvergencias.append("" if t is None else f"{t - c:.3f}")
    maximo, duracao = lideres_ao_mesmo_tempo(vidas, t_ini, t_fim)
    _, props = verificar(pasta)
    return {
        "reconvergencia_s": ";".join(reconvergencias),
        "lideres_max": maximo,
        "dois_lideres_s": round(duracao, 3),
        "avisos_verificador": ";".join(nome for ok, nome, _ in props if not ok),
    }


def executar(caminhos=None, repeticoes=5, saida="results/experimentos", logs="results/raw/cenarios",
             comando=None, aquecimento_s=AQUECIMENTO_S, **params):
    # roda cada cenário `repeticoes` vezes e grava results/experimentos/cenarios.csv + .meta.json
    caminhos = caminhos or sorted(glob.glob(os.path.join(RAIZ, "scenarios", "*.yaml")))
    cenarios = [carregar_cenario(c) for c in caminhos]
    params = {**PARAMS, **params}
    inicio = datetime.now(timezone.utc)
    caminho = os.path.join(saida, "cenarios.csv")
    linhas = []
    try:
        # repetições por fora, como nas grades da #6
        for r in range(1, repeticoes + 1):
            for cen in cenarios:
                seed = cen.get("seed", 0) + r - 1
                pasta = os.path.abspath(os.path.join(logs, f"{cen['name']}_seed{seed}"))
                t0 = time.time()
                t_ini, instantes, t_fim = rodar_cenario(cen, seed, pasta, aquecimento_s, **params)
                linha = {"cenario": cen["name"], "repeticao": r, "seed": seed, "n": N, **params,
                         "duracao_s": round(time.time() - t0, 2),
                         "eventos": ";".join(rotulo(e) for e in cen["events"]),
                         **medir(pasta, cen["events"], t_ini, instantes, t_fim)}
                linhas.append(linha)
                print(f"[cenarios] {cen['name']} rep {r}/{repeticoes}: reconvergência "
                      f"{linha['reconvergencia_s']} s, até {linha['lideres_max']} líderes "
                      f"({linha['dois_lideres_s']} s), avisos: {linha['avisos_verificador'] or '-'}",
                      flush=True)
    finally:
        escrever_csv(caminho, COLUNAS, linhas)
        meta = {
            "completa": len(linhas) == repeticoes * len(cenarios),
            **proveniencia(inicio, comando),
            "repeticoes": repeticoes,
            "parametros": {"n": N, **params},
            "execucao": {"aquecimento_s": aquecimento_s, "crash": "SIGKILL do processo",
                         "recover": "processo novo, mesma porta e mesmo log",
                         "rede": "partição, cura e perda pelo /control do nó (FaultInjector, issue #2)"},
            "cenarios": {os.path.relpath(c, RAIZ): cen for c, cen in zip(caminhos, cenarios)},
            "ambiente": ambiente(),
            "colunas": COLUNAS,
        }
        escrever_atomico(caminho + ".meta.json", json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    return linhas


def main(argv=None):
    p = argparse.ArgumentParser(description="Roda os cenários de falha e grava o CSV bruto")
    p.add_argument("cenarios", nargs="*", help="arquivos YAML (padrão: scenarios/*.yaml)")
    p.add_argument("--repeticoes", type=int, default=5)
    p.add_argument("--saida", default="results/experimentos")
    args = p.parse_args(argv)
    # SIGTERM (kill) vira SystemExit: os nós são parados e o que já rodou é gravado
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    executar([os.path.abspath(c) for c in args.cenarios] or None, args.repeticoes, args.saida,
             comando=" ".join(["python -m experiments.cenarios"] + (sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    main()
