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
#                                    se antes disso outro nó sobe ou cai, ou começa uma falha de rede,
#                                    nem se a mudança acontece com partição ou perda ativas (issue #7)
# Serviço de tarefas (issue #8, só se houver tarefas no log; lê também o cliente.jsonl da pasta):
#   tarefas.execucao   (vivacidade) toda tarefa enviada pelo cliente foi confirmada, e só depois de
#                                    um task_end dela em algum worker
#   tarefas.um_lider   (segurança)  nenhuma tarefa esteve com dois líderes ao mesmo tempo (do primeiro
#                                    task_assign de um pedido ao task_reply, ou ao fim da vida do líder)
#
# As propriedades valem no período síncrono. Com partição, perda ou atraso, um [AVISO] pode ser
# a consequência esperada da falha; o cabeçalho do relatório diz se houve falhas de rede no log
# e quantos nós se acharam líderes ao mesmo tempo (o Bully não garante um líder só).
import argparse
import glob
import json
import os
from itertools import combinations

from src.eventlog import read_events

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


def inicio_convergido(bs, c, ponto):
    # início do último trecho de [c, ponto] em que todos os vivos têm o maior vivo como líder;
    # None se em `ponto` ainda não convergiu. O estado só muda num "leader" ou quando um nó
    # sobe ou cai
    if not convergiu(bs, ponto):
        return None
    inicio = None
    mudancas = sorted(x for v in bs for x in [v.inicio, v.fim] + [t for t, _ in v.lideres] if c < x <= ponto)
    for x in [c] + mudancas:
        if not convergiu(bs, x):
            inicio = None
        elif inicio is None:
            inicio = x
    return inicio


def rede_com_falha(vidas, t):
    # algum nó vivo em t está com partição ou perda ligadas pelo /control: sem resposta, não há
    # período síncrono. Atraso e duplicação não contam: as chamadas continuam respondidas.
    # Um nó que volta começa sem falhas: elas ficam só na memória
    for v in vidas:
        if not v.inicio <= t < v.fim:
            continue
        bloqueados, falhas = set(), False
        for e in v.eventos:
            if e["ts"] > t:
                break
            d = e["data"]
            if e["event"] == "partition_start":
                bloqueados.add(d["peer"])
            elif e["event"] == "partition_end":
                bloqueados.discard(d["peer"])
            elif e["event"] == "faults_updated":
                falhas = d.get("drop_prob", 0) > 0
            elif e["event"] == "faults_reset":
                bloqueados, falhas = set(), False
        if bloqueados or falhas:
            return True
    return False


def lideres_ao_mesmo_tempo(vidas, t0, t1):
    # entre t0 e t1: quantos nós vivos se acharam líderes ao mesmo tempo, no máximo, e por
    # quanto tempo houve mais de um. Com partição, um por lado (split-brain); sem falhas, só
    # na troca de líder (docs/modelo_bully.md §5)
    bs = com_bully(vidas)
    pontos = sorted({t0, t1} | {x for v in bs for x in [v.inicio, v.fim] + [t for t, _ in v.lideres]
                                if t0 < x < t1})
    maximo, duracao = 0, 0.0
    for a, b in zip(pontos, pontos[1:]):
        k = sum(1 for v in bs if v.inicio <= a < v.fim and lider_em(v, a) == v.no)
        maximo = max(maximo, k)
        if k > 1:
            duracao += b - a
    return maximo, duracao


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
        if rede_com_falha(vidas, c):
            # ex.: nó cai durante uma partição; sem período síncrono não há prazo, e o fim
            # da falha de rede abre outra cobrança
            nao_conferidas += 1
            continue
        # confere no prazo, ou um instante antes da próxima perturbação, se ela vier antes
        # (nunca antes de c: processos locais podem subir a menos de EPS um do outro)
        h = min([c + prazo] + [p for p in perturbacoes if p > c])
        ponto = max(c, h - EPS)
        t = inicio_convergido(bs, c, ponto)
        if t is None:
            if h < c + prazo:
                nao_conferidas += 1   # perturbado antes do prazo: não dá para cobrar
            else:
                total += 1
                erros.append(f"não tinha convergido {prazo:.2f} s depois de {motivo} em +{c - t0:.2f}s")
            continue
        total += 1
        pior = max(pior, t - c)
    return resultado("bully.convergencia", total, erros,
                     f"{total} mudanças conferidas, convergência em até {pior:.2f} s (prazo "
                     f"{prazo:.2f} s); {nao_conferidas} não conferidas (outra mudança antes do prazo, "
                     f"ou durante partição ou perda)")


# --- serviço de tarefas (issue #8) ---

def ler_cliente(pasta):
    caminho = os.path.join(pasta, "cliente.jsonl")
    return read_events(caminho) if os.path.exists(caminho) else []


def tarefas_execucao(vidas, cliente, t0):
    if not cliente:
        return False, "tarefas.execucao", "nenhum cliente.jsonl na pasta: não dá para conferir"
    fins = {}
    for v in vidas:
        for e in v.do_tipo("task_end"):
            fins.setdefault(e["data"]["task"], []).append(e["ts"])
    enviadas = [e["data"]["id"] for e in cliente if e["event"] == "tarefa_enviada"]
    confirmadas = {e["data"]["id"]: e["ts"] for e in cliente if e["event"] == "tarefa_confirmada"}
    erros = []
    for k in enviadas:
        if k not in confirmadas:
            erros.append(f"tarefa {k} não foi confirmada")
        elif not any(t <= confirmadas[k] for t in fins.get(k, [])):
            erros.append(f"tarefa {k} confirmada em +{confirmadas[k] - t0:.2f}s sem ter sido executada")
    duplas = sum(1 for k in enviadas if len(fins.get(k, [])) > 1)
    return resultado("tarefas.execucao", len(enviadas), erros,
                     f"{len(enviadas)} tarefas enviadas, todas confirmadas depois de executadas "
                     f"({duplas} executadas mais de uma vez: semântica pelo menos uma vez)")


def tarefas_um_lider(vidas, t0):
    # um pedido segura a tarefa no líder do primeiro task_assign até o task_reply; se o líder cai
    # antes de responder, até o fim da vida dele
    pedidos = {}   # (vida, tarefa, pedido) -> [início, fim]
    for i, v in enumerate(vidas):
        for e in v.eventos:
            if e["event"] in ("task_assign", "task_reply"):
                chave = (i, e["data"]["task"], e["data"]["pedido"])
                if e["event"] == "task_assign":
                    pedidos.setdefault(chave, [e["ts"], v.fim])
                elif chave in pedidos:
                    pedidos[chave][1] = e["ts"]
    por_tarefa = {}
    for (i, tarefa, _), (a, b) in pedidos.items():
        por_tarefa.setdefault(tarefa, []).append((a, b, vidas[i].no))
    erros = []
    for tarefa, xs in sorted(por_tarefa.items()):
        for (a1, b1, l1), (a2, b2, l2) in combinations(sorted(xs), 2):
            if l1 != l2 and a2 < b1:
                erros.append(f"tarefa {tarefa} com os líderes {l1} e {l2} ao mesmo tempo em +{a2 - t0:.2f}s")
    return resultado("tarefas.um_lider", len(por_tarefa), erros,
                     f"{len(por_tarefa)} tarefas atribuídas, nenhuma com dois líderes ao mesmo tempo")


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
    ]
    if com_bully(vidas):
        maximo, duracao = lideres_ao_mesmo_tempo(vidas, t0, max(v.fim for v in vidas))
        cabecalho.append(f"Nós que se acharam líderes ao mesmo tempo: até {maximo} "
                         f"({duracao:.2f} s com mais de um)")
    cabecalho.append("Tempos em +s contados do primeiro node_start. As propriedades valem no período síncrono.")
    if detectores(vidas):
        props = [suspeita_apos_timeout(vidas, t0), alternancia(vidas, t0),
                 completude(vidas, t0, folga), restauracao(vidas, t0, folga)]
    else:
        props = [(False, "detector", "nenhum detector_start nos logs (HB_INTERVAL = 0?)")]
    if detectores(vidas) and com_bully(vidas):
        props += [acordo(vidas, t0), convergencia(vidas, t0, folga)]
    else:
        props += [(False, "bully", "nenhum bully_start nos logs (HB_INTERVAL = 0, ou logs de antes da issue #5)")]
    cliente = ler_cliente(pasta)
    if cliente or any(v.primeiro("task_assign") for v in vidas):
        props += [tarefas_execucao(vidas, cliente, t0), tarefas_um_lider(vidas, t0)]
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
