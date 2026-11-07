# Grade de experimentos (issue #6): roda ensaios com processos reais e mede o detector
# e o Bully a partir dos logs JSONL.
#
# Um ensaio (rodar_ensaio):
#   1. sobe n nós, cada um num processo próprio (python -m src.node) em 127.0.0.1;
#   2. espera todos terem o nó n como líder e mais `aquecimento_s` (aquecimento, fora das medidas);
#   3. mede `antes_s` segundos sem falha;
#   4. derruba o líder (nó n) com SIGKILL e mede mais `depois_s` segundos;
#   5. para os nós (SIGTERM).
# As medidas (medir) vêm dos logs do ensaio; os instantes do crash e do início e fim das
# medidas são anotados aqui. Todos os processos rodam na mesma máquina, então os "ts"
# dos logs e os daqui são comparáveis.
#
# Uma grade (execute_grid) varia um parâmetro em vários níveis, repete cada nível com
# sementes diferentes e grava um CSV (uma linha por ensaio) e um .csv.meta.json com a
# proveniência. A escrita é atômica (arquivo temporário e os.replace) e acontece no fim da
# grade, ou antes, se um ensaio falhar (o .meta.json diz se a grade ficou completa).
#
# Definições das métricas e colunas: docs/experimentos.md.
import csv
import io
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from analysis.log_verifier import ler_vidas, lider_em, verificar

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIPOS = ("ELECTION", "OK", "COORDINATOR")

# parâmetros de um ensaio e seus valores padrão (os do compose.yaml)
PADRAO = {"n": 5, "drop_prob": 0.0, "hb_intervalo": 0.5, "hb_timeout": 2.0, "rpc_timeout": 1.0}

COLUNAS = {
    "grade": "nome da grade",
    "repeticao": "número da repetição (1, 2, ...)",
    "seed": "SEED dos nós (cada nó usa SEED * 1000 + NODE_ID)",
    "n": "número de nós",
    "drop_prob": "probabilidade de perda de cada mensagem (e de cada resposta)",
    "hb_intervalo": "HB_INTERVAL (s)",
    "hb_timeout": "HB_TIMEOUT (s)",
    "rpc_timeout": "RPC_TIMEOUT (s), também o timeout do OK no Bully",
    "aquecimento_s": "espera depois de todos terem o líder n, fora das medidas (s)",
    "duracao_s": "duração total do ensaio, da subida dos processos ao fim (s)",
    "deteccao_s": "tempo do crash até cada sobrevivente suspeitar do nó n (s), separados por ';'; "
                  "vazio = nenhum suspect depois do crash (não suspeitou, ou já suspeitava antes dele)",
    "deteccao_max_s": "tempo do crash até o último sobrevivente suspeitar (s)",
    "convergencia_s": "tempo do crash até todos os sobreviventes terem o nó n-1 como líder (s); vazio = não convergiu",
    "eleicao_s": "tempo da primeira suspeita até a convergência (s); com perda pode ser negativo",
    "eleicoes": "eleições (election_end) começadas entre o crash e a estabilização",
    "msgs_election": "ELECTION enviados nessas eleições",
    "msgs_ok": "OK enviados nessas eleições",
    "msgs_coordinator": "COORDINATOR enviados nessas eleições",
    "msgs_bully": "total de mensagens do Bully nessas eleições",
    "falsos_positivos": "suspeitas de nós vivos durante as medidas",
    "fp_por_par_min": "falsos positivos por par monitorado (observador, peer) por minuto",
    "hb_msgs_por_s": "heartbeats enviados por segundo no sistema, antes do crash",
    "avisos_verificador": "propriedades com [AVISO] no analysis.log_verifier, separadas por ';'",
}


@dataclass
class GridConfig:
    nome: str                       # nome da grade, também o nome do CSV
    parametro: str                  # parâmetro variado (uma chave de PADRAO)
    niveis: list                    # valores do parâmetro
    base: dict = field(default_factory=dict)   # outros parâmetros fixos (o resto vem de PADRAO)
    repeticoes: int = 10
    semente0: int = 1               # a repetição r usa a semente semente0 + r - 1
    aquecimento_s: float = 2.0
    antes_s: float = 4.0
    depois_s: float = 10.0


@dataclass
class GridResults:
    config: GridConfig
    linhas: list                    # um dicionário por ensaio, com as chaves de COLUNAS
    csv: str
    meta: str


# --- um ensaio ---

def portas_livres(n):
    # o sistema escolhe portas livres; todas abertas juntas para não repetir
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    portas = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return portas


def lider(porta):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/status", timeout=0.5) as r:
            return json.loads(r.read())["lider"]
    except Exception:
        return None


def iniciar_no(pasta, i, portas, seed, drop_prob, hb_intervalo, hb_timeout, rpc_timeout):
    # sobe o nó i num processo próprio, escutando só em 127.0.0.1; portas: id -> porta
    env = dict(os.environ, NODE_ID=str(i), BIND_ADDR="127.0.0.1", PORT=str(portas[i]),
               PEERS=",".join(f"{j}=127.0.0.1:{p}" for j, p in portas.items()),
               SEED=str(seed), LOG_DIR=pasta, PING_INTERVAL="0",
               RPC_TIMEOUT=str(rpc_timeout), HB_INTERVAL=str(hb_intervalo),
               HB_TIMEOUT=str(hb_timeout), NET_DROP_PROB=str(drop_prob),
               NET_DELAY_MS="0", NET_JITTER_MS="0", NET_DUP_PROB="0")
    # "a": um nó que volta (cenários da #7) continua o mesmo arquivo
    with open(os.path.join(pasta, f"node{i}.err"), "a") as erros:
        return subprocess.Popen([sys.executable, "-m", "src.node"], cwd=RAIZ, env=env,
                                stdout=subprocess.DEVNULL, stderr=erros)


def esperar_lider(portas, esperado, limite_s, pasta):
    fim = time.monotonic() + limite_s
    while any(lider(p) != esperado for p in portas.values()):
        if time.monotonic() > fim:
            raise RuntimeError(f"os nós não elegeram o nó {esperado} em {limite_s} s (ver {pasta})")
        time.sleep(0.05)


def parar(procs):
    for p in procs.values():
        if p.poll() is None:
            p.send_signal(signal.SIGTERM)   # desligamento gracioso (node_stop no log)
    for p in procs.values():
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def rodar_ensaio(pasta, seed, n, drop_prob, hb_intervalo, hb_timeout, rpc_timeout,
                 aquecimento_s, antes_s, depois_s, limite_s=60.0):
    # devolve os instantes (time.time) de início das medidas, do crash e do fim
    shutil.rmtree(pasta, ignore_errors=True)
    os.makedirs(pasta)
    portas = dict(zip(range(1, n + 1), portas_livres(n)))
    procs = {}
    try:
        for i in portas:
            procs[i] = iniciar_no(pasta, i, portas, seed, drop_prob, hb_intervalo, hb_timeout, rpc_timeout)
        esperar_lider(portas, n, limite_s, pasta)
        time.sleep(aquecimento_s)
        t_ini = time.time()
        time.sleep(antes_s)
        t_crash = time.time()
        procs[n].kill()                 # SIGKILL: o nó morre sem rodar código de encerramento
        procs[n].wait()
        time.sleep(depois_s)
        t_fim = time.time()
    finally:
        parar(procs)
    return t_ini, t_crash, t_fim


# --- medidas a partir dos logs ---

def medir(pasta, n, t_ini, t_crash, t_fim):
    vidas = {v.no: v for v in ler_vidas(pasta)}
    vivos = [vidas[i] for i in range(1, n)]
    novo = n - 1

    # detector: tempo de detecção, falsos positivos e heartbeats por segundo
    suspeitas = []
    for v in vivos:
        ts = [e["ts"] for e in v.do_tipo("suspect") if e["data"]["peer"] == n and e["ts"] > t_crash]
        suspeitas.append(ts[0] if ts else None)
    deteccoes = [None if s is None else s - t_crash for s in suspeitas]
    falsos = sum(1 for v in vidas.values() for e in v.do_tipo("suspect")
                 if t_ini <= e["ts"] <= t_fim and (e["data"]["peer"] != n or e["ts"] < t_crash))
    pares_s = n * (n - 1) * (t_crash - t_ini) + (n - 1) * (n - 2) * (t_fim - t_crash)
    heartbeats = sum(1 for v in vidas.values() for e in v.eventos
                     if t_ini <= e["ts"] < t_crash and e["data"].get("msg") == "heartbeat"
                     and ((e["event"] == "send" and not e["data"].get("dup"))
                          or (e["event"] == "drop" and e["data"].get("reason") != "reply_loss")))

    # Bully: primeiro instante, depois do crash, em que todos os sobreviventes têm o nó n-1 como líder
    candidatos = sorted(t for v in vivos for t, _ in v.lideres if t > t_crash)
    t_conv = next((t for t in candidatos if all(lider_em(v, t) == novo for v in vivos)), None)
    primeira = min((s for s in suspeitas if s is not None), default=None)

    # mensagens das eleições começadas entre o crash e a estabilização (convergência e
    # todas as detecções): inclui as rodadas extras do novo líder (docs/modelo_bully.md §3)
    estavel = max([t_conv if t_conv is not None else t_fim] + [s for s in suspeitas if s is not None])
    msgs, eleicoes = dict.fromkeys(TIPOS, 0), 0
    for v in vivos:
        inicio = None
        for e in v.eventos:
            if e["event"] == "election_start" and inicio is None:
                inicio = e["ts"]                # uma eleição pode ter várias rodadas
            elif e["event"] == "election_end":
                if inicio is not None and t_crash < inicio <= estavel:
                    eleicoes += 1
                    for tipo in TIPOS:
                        msgs[tipo] += e["data"]["msgs"].get(tipo, 0)
                inicio = None

    _, props = verificar(pasta)
    return {
        "deteccao_s": ";".join("" if d is None else f"{d:.3f}" for d in deteccoes),
        "deteccao_max_s": None if None in deteccoes else round(max(deteccoes), 3),
        "convergencia_s": None if t_conv is None else round(t_conv - t_crash, 3),
        "eleicao_s": None if t_conv is None or primeira is None else round(t_conv - primeira, 3),
        "eleicoes": eleicoes,
        "msgs_election": msgs["ELECTION"],
        "msgs_ok": msgs["OK"],
        "msgs_coordinator": msgs["COORDINATOR"],
        "msgs_bully": sum(msgs.values()),
        "falsos_positivos": falsos,
        "fp_por_par_min": round(falsos / pares_s * 60, 4),
        "hb_msgs_por_s": round(heartbeats / (t_crash - t_ini), 2),
        "avisos_verificador": ";".join(nome for ok, nome, _ in props if not ok),
    }


# --- grade ---

def escrever_atomico(caminho, texto):
    os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(texto)
    os.replace(tmp, caminho)


def git(*args):
    try:
        return subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def cpu_modelo():
    try:
        if sys.platform == "darwin":
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                  capture_output=True, text=True, timeout=5).stdout.strip()
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for linha in f:
                if linha.startswith("model name"):
                    return linha.split(":", 1)[1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return platform.processor()


def ambiente():
    try:
        memoria = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (AttributeError, ValueError, OSError):
        memoria = None
    return {"so": platform.platform(), "maquina": platform.machine(), "cpu": cpu_modelo(),
            "cpus": os.cpu_count(), "memoria_gb": memoria,
            "python": f"{platform.python_implementation()} {platform.python_version()}",
            "execucao": "um processo por nó (python -m src.node), todos em 127.0.0.1 na mesma máquina"}


def execute_grid(cfg, saida="results/experimentos", logs="results/raw/experimentos", comando=None):
    inicio = datetime.now(timezone.utc)
    caminho = os.path.join(saida, f"{cfg.nome}.csv")
    linhas = []
    try:
        # repetições por fora: uma mudança de carga na máquina não cai toda num nível só
        for r in range(1, cfg.repeticoes + 1):
            for nivel in cfg.niveis:
                p = {**PADRAO, **cfg.base, cfg.parametro: nivel}
                seed = cfg.semente0 + r - 1
                pasta = os.path.abspath(os.path.join(logs, cfg.nome, f"{cfg.parametro}={nivel}_seed{seed}"))
                t0 = time.time()
                t_ini, t_crash, t_fim = rodar_ensaio(pasta, seed, aquecimento_s=cfg.aquecimento_s,
                                                     antes_s=cfg.antes_s, depois_s=cfg.depois_s, **p)
                linha = {"grade": cfg.nome, "repeticao": r, "seed": seed, **p,
                         "aquecimento_s": cfg.aquecimento_s, "duracao_s": round(time.time() - t0, 2),
                         **medir(pasta, p["n"], t_ini, t_crash, t_fim)}
                linhas.append(linha)
                print(f"[{cfg.nome}] {cfg.parametro}={nivel} rep {r}/{cfg.repeticoes}: "
                      f"detecção {linha['deteccao_max_s']} s, convergência {linha['convergencia_s']} s, "
                      f"{linha['msgs_bully']} msgs do Bully, {linha['falsos_positivos']} falsos positivos",
                      flush=True)
    finally:
        # grava o que já rodou mesmo se um ensaio falhar; "completa" diz se a grade terminou
        gravar(cfg, linhas, caminho, inicio, comando)
    return GridResults(cfg, linhas, caminho, caminho + ".meta.json")


def escrever_csv(caminho, colunas, linhas):
    texto = io.StringIO()
    escritor = csv.DictWriter(texto, fieldnames=list(colunas), lineterminator="\n")
    escritor.writeheader()
    escritor.writerows(linhas)
    escrever_atomico(caminho, texto.getvalue())


def proveniencia(inicio, comando):
    fim = datetime.now(timezone.utc)
    return {
        "comando": comando or " ".join(["python -m experiments.run_all"] + sys.argv[1:]),
        "git_commit": git("rev-parse", "HEAD"),
        # results/ fica de fora: os próprios CSVs mudam a cada execução
        "git_alteracoes_locais": git("status", "--porcelain", "--", ".", ":(exclude)results") != "",
        "inicio_utc": inicio.isoformat(timespec="seconds"),
        "fim_utc": fim.isoformat(timespec="seconds"),
        "duracao_total_s": round((fim - inicio).total_seconds(), 1),
    }


def gravar(cfg, linhas, caminho, inicio, comando):
    escrever_csv(caminho, COLUNAS, linhas)
    meta = {
        "grade": cfg.nome,
        "completa": len(linhas) == cfg.repeticoes * len(cfg.niveis),
        **proveniencia(inicio, comando),
        "config": asdict(cfg),
        "parametros_padrao": PADRAO,
        "ensaio": {"aquecimento_s": cfg.aquecimento_s, "antes_do_crash_s": cfg.antes_s,
                   "depois_do_crash_s": cfg.depois_s, "falha": "SIGKILL do nó n (o líder)"},
        "ambiente": ambiente(),
        "colunas": COLUNAS,
    }
    escrever_atomico(caminho + ".meta.json", json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
