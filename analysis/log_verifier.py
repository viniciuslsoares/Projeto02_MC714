# Verificador de segurança e vivacidade a partir dos logs JSONL (issue #5).
#
# Uso (da raiz do repositório):
#   python -m analysis.log_verifier [pasta_dos_logs] [--saida results/verifications.txt] [--folga 0.5]
# Lê todos os node*.jsonl da pasta (padrão: results/logs) e escreve uma linha [OK] ou
# [AVISO] por propriedade. Propriedades e limites: docs/modelo_detector.md e
# docs/modelo_bully.md; como cada uma é conferida: docs/testes.md.
#
# Cada arquivo é dividido em "vidas" (encarnações do nó): do node_start até o node_stop,
# ou até o último evento, se o processo morreu sem node_stop (SIGKILL). O nó está vivo
# em [inicio, fim) de cada vida. Todos os nós rodam na mesma máquina, então os
# instantes ("ts") dos logs de nós diferentes são comparáveis.
#
# Detector (em cada vida de cada observador, para cada peer):
#   detector.suspeita_apos_timeout (segurança)  suspect só depois de mais de timeout sem heartbeat
#   detector.alternancia           (segurança)  suspect e restore se alternam
#   detector.completude            (vivacidade) todo silêncio maior que timeout + intervalo + folga
#                                               tem um suspect dentro desse prazo
#   detector.restauracao           (vivacidade) depois de um suspect, o restore vem em até
#                                               intervalo + folga após o heartbeat seguinte
# Bully:
#   bully.acordo        (segurança)  todo "leader" decide um líder maior ou igual a todo nó que
#                                    ficou vivo durante a eleição que o elegeu
#   bully.convergencia  (vivacidade) depois que um nó sobe ou cai, ou que uma falha de rede termina,
#                                    todos os vivos têm o maior vivo como líder em até
#                                    HB_TIMEOUT + HB_INTERVAL + 2 * RPC_TIMEOUT + folga; só não se cobra
#                                    se antes disso outro nó sobe ou cai, ou começa uma falha de rede
#
# As propriedades valem no período síncrono. Com partição, perda ou atraso, um [AVISO] pode ser
# a consequência esperada da falha; o cabeçalho do relatório diz se houve falhas de rede no log.
import argparse
import glob
import json
import os

FOLGA = 0.5     # s: atraso de escalonamento admitido nos prazos de vivacidade
MARGEM = 0.05   # s: um heartbeat logado logo antes do suspect pode ainda não ter sido anotado
EPS = 1e-6      # s: "um instante antes" de uma mudança
PERTURBA = ("partition_start", "faults_updated")   # falha de rede começa
CURA = ("partition_end", "faults_reset")            # falha de rede termina
REDE = PERTURBA + CURA


class Vida:
    def __init__(self, no, inicio):
        self.no = no
        self.inicio = inicio
        self.fim = inicio
        self.eventos = []
        self.lideres = []   # (ts, líder) de cada evento "leader"

    def do_tipo(self, tipo):
        return [e for e in self.eventos if e["event"] == tipo]

    def primeiro(self, tipo):
        eventos = self.do_tipo(tipo)
        return eventos[0] if eventos else None

    def heartbeats(self, peer):
        return [e["ts"] for e in self.eventos if e["event"] == "recv"
                and e["data"].get("msg") == "heartbeat" and e["data"].get("peer") == peer]


def ler_vidas(pasta):
    vidas = []
    for caminho in sorted(glob.glob(os.path.join(pasta, "node*.jsonl"))):
        eventos = []
        with open(caminho, encoding="utf-8") as f:
            for linha in f:
                try:
                    eventos.append(json.loads(linha))
                except ValueError:
                    pass   # linha vazia ou cortada por um SIGKILL
        parou = True
        for e in eventos:
            if e["event"] == "node_start":
                vidas.append(Vida(e["node"], e["ts"]))
                parou = False
            if not parou:
                vidas[-1].eventos.append(e)
            if e["event"] == "node_stop":
                parou = True
    for v in vidas:
        v.eventos.sort(key=lambda e: e["ts"])
        v.fim = v.eventos[-1]["ts"]
        v.lideres = [(e["ts"], e["data"]["lider"]) for e in v.eventos if e["event"] == "leader"]
    return vidas


def resultado(nome, total, erros, texto_ok):
    if not erros:
        return True, nome, texto_ok
    exemplos = "; ".join(erros[:3]) + ("; ..." if len(erros) > 3 else "")
    return False, nome, f"{len(erros)} de {total}: {exemplos}"


# --- detector ---

def detectores(vidas):
    return [(v, v.primeiro("detector_start")) for v in vidas if v.primeiro("detector_start")]


def suspeita_apos_timeout(vidas, t0):
    total, erros = 0, []
    for v, d in detectores(vidas):
        for e in v.do_tipo("suspect"):
            peer = e["data"]["peer"]
            antes = [t for t in v.heartbeats(peer) if t < e["ts"] - MARGEM]
            sem_heartbeat = e["ts"] - max(antes + [d["ts"]])
            total += 1
            if sem_heartbeat < d["data"]["timeout"] - MARGEM:
                erros.append(f"nó {v.no} suspeitou de {peer} em +{e['ts'] - t0:.2f}s "
                             f"com só {sem_heartbeat:.2f} s sem heartbeat")
    return resultado("detector.suspeita_apos_timeout", total, erros,
                     f"{total} suspeitas, todas depois de mais de timeout sem heartbeat")


def alternancia(vidas, t0):
    total, erros = 0, []
    for v, _ in detectores(vidas):
        suspeitos = set()
        for e in v.eventos:
            if e["event"] not in ("suspect", "restore"):
                continue
            peer = e["data"]["peer"]
            total += 1
            if (e["event"] == "suspect") == (peer in suspeitos):
                erros.append(f"nó {v.no}: {e['event']} de {peer} repetido em +{e['ts'] - t0:.2f}s")
            if e["event"] == "suspect":
                suspeitos.add(peer)
            else:
                suspeitos.discard(peer)
    return resultado("detector.alternancia", total, erros,
                     f"{total} avisos suspect/restore, sempre alternados")


def completude(vidas, t0, folga):
    total, erros = 0, []
    for v, d in detectores(vidas):
        prazo = d["data"]["timeout"] + d["data"]["intervalo"] + folga
        # logs de antes da issue #5 não têm "peers" no detector_start
        for peer in d["data"].get("peers", v.primeiro("node_start")["data"].get("peers", [])):
            marcos = [d["ts"]] + [t for t in v.heartbeats(peer) if t > d["ts"]] + [v.fim]
            suspeitas = [e["ts"] for e in v.do_tipo("suspect") if e["data"]["peer"] == peer]
            for a, b in zip(marcos, marcos[1:]):
                if b - a > prazo:
                    total += 1
                    if not any(a < s <= a + prazo for s in suspeitas):
                        erros.append(f"nó {v.no} não suspeitou de {peer} a tempo depois de "
                                     f"+{a - t0:.2f}s ({b - a:.2f} s sem heartbeat)")
    return resultado("detector.completude", total, erros,
                     f"{total} silêncios maiores que timeout + intervalo + folga, todos com suspeita a tempo")


def restauracao(vidas, t0, folga):
    total, erros = 0, []
    for v, d in detectores(vidas):
        prazo = d["data"]["intervalo"] + folga
        abertas = {}   # peer suspeito -> primeiro heartbeat depois do suspect (None se não veio)
        for e in v.eventos:
            peer = e["data"].get("peer")
            if e["event"] == "suspect":
                abertas[peer] = None
            elif (e["event"] == "recv" and e["data"].get("msg") == "heartbeat"
                  and peer in abertas and abertas[peer] is None):
                abertas[peer] = e["ts"]
            elif e["event"] == "restore" and peer in abertas:
                heartbeat = abertas.pop(peer)
                total += 1
                if heartbeat is not None and e["ts"] - heartbeat > prazo:
                    erros.append(f"nó {v.no} restaurou {peer} {e['ts'] - heartbeat:.2f} s "
                                 f"depois do heartbeat de +{heartbeat - t0:.2f}s")
        for peer, heartbeat in abertas.items():
            if heartbeat is not None and v.fim - heartbeat > prazo:
                total += 1
                erros.append(f"nó {v.no} não restaurou {peer} depois do heartbeat de "
                             f"+{heartbeat - t0:.2f}s")
    return resultado("detector.restauracao", total, erros,
                     f"{total} suspeitas retiradas, todas a tempo depois do heartbeat seguinte")


# --- Bully ---

def com_bully(vidas):
    return [v for v in vidas if v.primeiro("bully_start")]


def lider_em(v, t):
    lider = None
    for ts, l in v.lideres:
        if ts > t:
            break
        lider = l
    return lider


def acordo(vidas, t0):
    bs = com_bully(vidas)
    total, erros = 0, []
    for v in bs:
        for t, lider in v.lideres:
            # a eleição que elegeu `lider` começou no último election_start dele antes de t
            inicios = [e["ts"] for w in bs if w.no == lider
                       for e in w.do_tipo("election_start") if e["ts"] <= t]
            inicio = max(inicios, default=t)
            maiores = sorted({w.no for w in bs if w.no > lider and w.inicio <= inicio and w.fim >= t})
            total += 1
            if maiores:
                erros.append(f"nó {v.no} decidiu {lider} em +{t - t0:.2f}s com {maiores} vivo(s)")
    return resultado("bully.acordo", total, erros,
                     f"{total} decisões de líder, nenhuma com um nó maior vivo durante a eleição")


def convergiu(bs, t):
    vivos = [v for v in bs if v.inicio <= t < v.fim]
    if not vivos:
        return True
    maior = max(v.no for v in vivos)
    return all(lider_em(v, t) == maior for v in vivos)


def convergencia(vidas, t0, folga):
    bs = com_bully(vidas)
    intervalo = max(d["data"]["intervalo"] for _, d in detectores(vidas))
    timeout_hb = max(d["data"]["timeout"] for _, d in detectores(vidas))
    rpc = max(v.primeiro("bully_start")["data"]["timeout"] for v in bs)
    prazo = timeout_hb + intervalo + 2 * rpc + folga
    nos = [(v.inicio, f"início do nó {v.no}") for v in bs] + [(v.fim, f"fim do nó {v.no}") for v in bs]
    rede = [(e["ts"], e["event"], v.no) for v in vidas for e in v.eventos if e["event"] in REDE]
    # nó sobe ou cai, ou falha de rede começa: cancela a cobrança anterior que ainda não venceu
    perturbacoes = sorted([t for t, _ in nos] + [t for t, ev, _ in rede if ev in PERTURBA])
    # nó sobe ou cai, ou falha de rede termina: abre uma cobrança de convergência
    cobrancas = sorted(nos + [(t, f"{ev} no nó {no}") for t, ev, no in rede if ev in CURA])
    total, erros, pior, nao_conferidas, anterior = 0, [], 0.0, 0, None
    for c, motivo in cobrancas:
        if c == anterior:
            continue
        anterior = c
        # confere no prazo, ou um instante antes da próxima perturbação, se ela vier antes
        # (nunca antes de c: processos locais podem subir a menos de EPS um do outro)
        h = min([c + prazo] + [p for p in perturbacoes if p > c])
        ponto = max(c, h - EPS)
        if not convergiu(bs, ponto):
            if h < c + prazo:
                nao_conferidas += 1   # perturbado antes do prazo: não dá para cobrar
            else:
                total += 1
                erros.append(f"não tinha convergido {prazo:.2f} s depois de {motivo} em +{c - t0:.2f}s")
            continue
        total += 1
        # entre c e h só os "leader" mudam o estado; mede até o início do último trecho convergido
        t = None
        for x in [c] + sorted(t for v in bs for t, _ in v.lideres if c < t <= ponto):
            if not convergiu(bs, x):
                t = None
            elif t is None:
                t = x
        pior = max(pior, t - c)
    return resultado("bully.convergencia", total, erros,
                     f"{total} mudanças conferidas, convergência em até {pior:.2f} s (prazo "
                     f"{prazo:.2f} s); {nao_conferidas} não conferidas (outra mudança antes do prazo)")


def verificar(pasta, folga=FOLGA):
    # devolve o cabeçalho e uma lista de (ok, nome, texto), uma por propriedade
    vidas = ler_vidas(pasta)
    if not vidas:
        return [f"Nenhum node_start em {pasta}"], [(False, "logs", "nenhum log para verificar")]
    t0 = min(v.inicio for v in vidas)
    contagem = {}
    for v in vidas:
        for e in v.eventos:
            if e["event"] in REDE + ("drop",):
                contagem[e["event"]] = contagem.get(e["event"], 0) + 1
    rede = ", ".join(f"{n} {tipo}" for tipo, n in sorted(contagem.items())) or "nenhuma"
    cabecalho = [
        f"Logs: {pasta} ({len({v.no for v in vidas})} nós, {len(vidas)} vidas, "
        f"{sum(len(v.eventos) for v in vidas)} eventos, {max(v.fim for v in vidas) - t0:.1f} s)",
        f"Folga dos prazos: {folga} s. Falhas de rede no log: {rede}",
        "Tempos em +s contados do primeiro node_start. As propriedades valem no período síncrono.",
    ]
    if detectores(vidas):
        props = [suspeita_apos_timeout(vidas, t0), alternancia(vidas, t0),
                 completude(vidas, t0, folga), restauracao(vidas, t0, folga)]
    else:
        props = [(False, "detector", "nenhum detector_start nos logs (HB_INTERVAL = 0?)")]
    if detectores(vidas) and com_bully(vidas):
        props += [acordo(vidas, t0), convergencia(vidas, t0, folga)]
    else:
        props += [(False, "bully", "nenhum bully_start nos logs (HB_INTERVAL = 0, ou logs de antes da issue #5)")]
    return cabecalho, props


def formatar(cabecalho, props):
    linhas = cabecalho + [""]
    for ok, nome, texto in props:
        linhas.append(f"{'[OK]   ' if ok else '[AVISO]'} {nome}: {texto}")
    return "\n".join(linhas) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="Verifica segurança e vivacidade nos logs JSONL")
    p.add_argument("pasta", nargs="?", default="results/logs")
    p.add_argument("--saida", default="results/verifications.txt")
    p.add_argument("--folga", type=float, default=FOLGA)
    args = p.parse_args(argv)
    texto = formatar(*verificar(args.pasta, args.folga))
    os.makedirs(os.path.dirname(args.saida) or ".", exist_ok=True)
    with open(args.saida, "w", encoding="utf-8") as f:
        f.write(texto)
    print(texto, end="")
    print(f"(salvo em {args.saida})")


if __name__ == "__main__":
    main()
