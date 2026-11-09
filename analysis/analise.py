# Script de consolidação estatística, geração de figuras e tabelas LaTeX (Issue #9).
#
# Processa os dados brutos versionados em results/experimentos/ e gera:
#   1. Estatísticas descritivas com tendência central (média, mediana), variabilidade
#      (desvio padrão, IC95%) e percentis (p50, p95, p99) para latências.
#   2. Figuras em PDF (vetorial para o relatório IEEE) e PNG (pré-visualização).
#   3. Tabelas formatadas em LaTeX com booktabs prontas para inclusão no relatório.
#
# Uso (da raiz do repositório):
#   python -m analysis.analise
#   python -m analysis.analise --dados results/experimentos --saida results/analise
import argparse
import csv
import json
import math
import os
import sys
import tempfile

# Configura cache seguro do Matplotlib antes de importar pyplot
if "MPLCONFIGDIR" not in os.environ:
    mpl_dir = os.path.join(tempfile.gettempdir(), "matplotlib_mc714")
    os.makedirs(mpl_dir, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = mpl_dir

import matplotlib.pyplot as plt
import numpy as np
import scipy.stats as st

# Configuração de estilo visual para gráficos padrão publicação (IEEE)
plt.rcParams.update({
    "font.size": 9,
    "axes.labelsize": 10,
    "axes.titlesize": 10,
    "xtick.labelsize": 8.5,
    "ytick.labelsize": 8.5,
    "legend.fontsize": 8.5,
    "figure.titlesize": 11,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "font.family": "serif",
    "axes.grid": True,
    "grid.alpha": 0.4,
    "grid.linestyle": ":",
})


# ==============================================================================
# 1. Funções de Apoio Estatístico
# ==============================================================================

def to_float(val, padrao=None):
    """Converte valor para float tratando vazios e erros."""
    if val is None or val == "":
        return padrao
    try:
        return float(val)
    except (ValueError, TypeError):
        return padrao


def estatisticas(amostras):
    """Calcula estatísticas descritivas completas para uma lista de amostras numéricas.

    Retorna um dicionário com:
      - n: número de observações
      - media: média aritmética
      - std: desvio padrão amostral (ddof=1)
      - ic95: margem do intervalo de confiança de 95% (t-Student)
      - mediana: p50
      - p95, p99: percentis 95 e 99
      - min, max: extremos
    """
    arr = np.array([x for x in amostras if x is not None and not math.isnan(x)], dtype=float)
    n = len(arr)
    if n == 0:
        return {
            "n": 0, "media": 0.0, "std": 0.0, "ic95": 0.0,
            "mediana": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0,
            "min": 0.0, "max": 0.0
        }

    media = float(np.mean(arr))
    if n > 1:
        std = float(np.std(arr, ddof=1))
        # Intervalo de confiança t de Student bilateral a 95%
        t_crit = float(st.t.ppf(0.975, df=n - 1))
        ic95 = t_crit * (std / math.sqrt(n))
    else:
        std = 0.0
        ic95 = 0.0

    p50 = float(np.percentile(arr, 50))
    p95 = float(np.percentile(arr, 95))
    p99 = float(np.percentile(arr, 99))

    return {
        "n": n,
        "media": media,
        "std": std,
        "ic95": ic95,
        "mediana": p50,
        "p50": p50,
        "p95": p95,
        "p99": p99,
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }


def fmt_media_ic(stats, decimais=2):
    r"""Formata 'media \pm ic95' para texto e LaTeX."""
    m = round(stats["media"], decimais)
    ic = round(stats["ic95"], decimais)
    return f"{m:.{decimais}f} \\pm {ic:.{decimais}f}"


def fmt_p50_p95(stats, decimais=2):
    """Formata 'p50 / p95' para tabelas."""
    p50 = round(stats["p50"], decimais)
    p95 = round(stats["p95"], decimais)
    return f"{p50:.{decimais}f} / {p95:.{decimais}f}"


def carregar_csv(caminho):
    """Lê arquivo CSV e retorna lista de dicionários."""
    if not os.path.exists(caminho):
        return []
    with open(caminho, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ==============================================================================
# 2. Processamento das Grades e Experimentos
# ==============================================================================

def analisar_grade_nos(linhas):
    """Agrega os ensaios da grade de nós (variação de N com perda = 0)."""
    grupos = {}
    for l in linhas:
        n = int(l["n"])
        grupos.setdefault(n, []).append(l)

    resultados = {}
    for n in sorted(grupos):
        amostras = grupos[n]
        det = [to_float(l["deteccao_max_s"]) for l in amostras]
        conv = [to_float(l["convergencia_s"]) for l in amostras]
        eleicao = [to_float(l["eleicao_s"]) for l in amostras]
        msgs_tot = [to_float(l["msgs_bully"]) for l in amostras]
        msgs_ele = [to_float(l["msgs_election"]) for l in amostras]
        msgs_ok = [to_float(l["msgs_ok"]) for l in amostras]
        msgs_coord = [to_float(l["msgs_coordinator"]) for l in amostras]
        hb_taxa = [to_float(l["hb_msgs_por_s"]) for l in amostras]
        fp = [to_float(l["falsos_positivos"]) for l in amostras]

        # Pior caso teórico do Bully com N nós sob queda do líder: N*(N-1)
        teorico_bully_pior = n * (n - 1)
        # Taxa teórica de heartbeats por segundo: N*(N-1) / intervalo (0.5 s) = 2*N*(N-1)
        teorico_hb = n * (n - 1) / 0.5

        resultados[n] = {
            "n": n,
            "repeticoes": len(amostras),
            "deteccao_max": estatisticas(det),
            "convergencia": estatisticas(conv),
            "eleicao": estatisticas(eleicao),
            "msgs_bully": estatisticas(msgs_tot),
            "msgs_election": estatisticas(msgs_ele),
            "msgs_ok": estatisticas(msgs_ok),
            "msgs_coordinator": estatisticas(msgs_coord),
            "hb_msgs_s": estatisticas(hb_taxa),
            "falsos_positivos": estatisticas(fp),
            "teorico_bully_pior": teorico_bully_pior,
            "teorico_hb": teorico_hb,
        }
    return resultados


def analisar_grade_perda(linhas):
    """Agrega os ensaios da grade de perda (variação de drop_prob com N = 5)."""
    grupos = {}
    for l in linhas:
        p = float(l["drop_prob"])
        grupos.setdefault(p, []).append(l)

    resultados = {}
    for p in sorted(grupos):
        amostras = grupos[p]
        det = [to_float(l["deteccao_max_s"]) for l in amostras]
        conv_validos = [to_float(l["convergencia_s"]) for l in amostras if l["convergencia_s"] != ""]
        eleicao = [to_float(l["eleicao_s"]) for l in amostras if l["eleicao_s"] != ""]
        msgs_tot = [to_float(l["msgs_bully"]) for l in amostras]
        fp = [to_float(l["falsos_positivos"]) for l in amostras]
        fp_taxa = [to_float(l["fp_por_par_min"]) for l in amostras]

        avisos = [l["avisos_verificador"] for l in amostras if l["avisos_verificador"]]

        resultados[p] = {
            "drop_prob": p,
            "repeticoes": len(amostras),
            "convergiram": len(conv_validos),
            "taxa_convergencia_pct": (len(conv_validos) / len(amostras)) * 100,
            "deteccao_max": estatisticas(det),
            "convergencia": estatisticas(conv_validos),
            "eleicao": estatisticas(eleicao),
            "msgs_bully": estatisticas(msgs_tot),
            "falsos_positivos": estatisticas(fp),
            "fp_por_par_min": estatisticas(fp_taxa),
            "total_avisos": len(avisos),
        }
    return resultados


def analisar_cenarios(linhas):
    """Agrega os ensaios dos cenários declarativos de falha e partição."""
    grupos = {}
    for l in linhas:
        c = l["cenario"]
        grupos.setdefault(c, []).append(l)

    resultados = {}
    for c in sorted(grupos):
        amostras = grupos[c]
        dois_lideres = [to_float(l["dois_lideres_s"]) for l in amostras]
        lideres_max = [to_float(l["lideres_max"]) for l in amostras]

        # Extrai os tempos de reconvergência para cada evento do cenário
        eventos_str = amostras[0]["eventos"].split(";")
        reconv_por_evento = [[] for _ in eventos_str]
        for l in amostras:
            tempos = l["reconvergencia_s"].split(";")
            for idx, t in enumerate(tempos):
                if idx < len(reconv_por_evento) and t != "":
                    reconv_por_evento[idx].append(float(t))

        avisos_set = set()
        for l in amostras:
            if l["avisos_verificador"]:
                for av in l["avisos_verificador"].split(";"):
                    if av.strip():
                        avisos_set.add(av.strip())

        reconv_stats = [estatisticas(am) for am in reconv_por_evento]

        resultados[c] = {
            "cenario": c,
            "repeticoes": len(amostras),
            "eventos": eventos_str,
            "reconvergencia_por_evento": reconv_stats,
            "dois_lideres_s": estatisticas(dois_lideres),
            "lideres_max": estatisticas(lideres_max),
            "avisos": sorted(list(avisos_set)),
        }
    return resultados


def analisar_integracao(linhas_sumario, linhas_tarefas):
    """Agrega métricas do serviço de tarefas integrando Bully e Heartbeat."""
    sumario = {}
    for l in linhas_sumario:
        c = l["cenario"]
        sumario.setdefault(c, []).append(l)

    resultados_sumario = {}
    for c in sorted(sumario):
        amostras = sumario[c]
        vazao = [to_float(l["vazao_s"]) for l in amostras]
        p50 = [to_float(l["lat_p50_ms"]) for l in amostras]
        p95 = [to_float(l["lat_p95_ms"]) for l in amostras]
        p99 = [to_float(l["lat_p99_ms"]) for l in amostras]
        lat_max = [to_float(l["lat_max_ms"]) for l in amostras]
        pausa = [to_float(l["maior_pausa_s"]) for l in amostras]
        reatrib = [to_float(l["reatribuidas"]) for l in amostras]
        exec2x = [to_float(l["executadas_2x"]) for l in amostras]

        resultados_sumario[c] = {
            "cenario": c,
            "repeticoes": len(amostras),
            "vazao": estatisticas(vazao),
            "p50_ms": estatisticas(p50),
            "p95_ms": estatisticas(p95),
            "p99_ms": estatisticas(p99),
            "lat_max_ms": estatisticas(lat_max),
            "pausa_s": estatisticas(pausa),
            "reatribuidas": estatisticas(reatrib),
            "executadas_2x": estatisticas(exec2x),
        }

    # Distribuição individual de todas as tarefas confirmadas
    tarefas_por_cenario = {}
    for t in linhas_tarefas:
        c = t["cenario"]
        lat_ms = float(t["latencia_s"]) * 1000.0
        tarefas_por_cenario.setdefault(c, []).append(lat_ms)

    return resultados_sumario, tarefas_por_cenario


# ==============================================================================
# 3. Geração de Tabelas LaTeX (.tex)
# ==============================================================================

def gerar_tabelas_latex(grade_nos, grade_perda, cenarios, integracao_sumario, pasta_saida):
    """Gera arquivos .tex compatíveis com IEEE booktabs."""
    os.makedirs(pasta_saida, exist_ok=True)

    # 1. Tabela: Grade de Nós
    caminho_tab1 = os.path.join(pasta_saida, "tab_grade_nos.tex")
    with open(caminho_tab1, "w", encoding="utf-8") as f:
        f.write("% Tabela gerada automaticamente por analysis/analise.py\n")
        f.write("\\begin{table}[htbp]\n")
        f.write("\\caption{Impacto da Escala ($N$) na Detecção, Eleição e Mensagens}\n")
        f.write("\\label{tab:grade_nos}\n")
        f.write("\\centering\n")
        f.write("\\small\n")
        f.write("\\begin{tabular}{@{}ccccccc@{}}\n")
        f.write("\\toprule\n")
        f.write("$N$ & Detecção (s) & Convergência (s) & Mensagens Bully & Teórico Pior & Heartbeats/s & Teórico HB \\\\\n")
        f.write(" & (Média $\\pm$ IC95\\%) & (Média $\\pm$ IC95\\%) & (Média $\\pm$ IC95\\%) & $N(N-1)$ & (Média) & $N(N-1)/\\Delta t$ \\\\\n")
        f.write("\\midrule\n")
        for n, r in grade_nos.items():
            det = fmt_media_ic(r["deteccao_max"])
            conv = fmt_media_ic(r["convergencia"])
            bully = fmt_media_ic(r["msgs_bully"], 1)
            hb = f"{r['hb_msgs_s']['media']:.1f}"
            f.write(f"{n} & ${det}$ & ${conv}$ & ${bully}$ & {r['teorico_bully_pior']} & {hb} & {r['teorico_hb']:.0f} \\\\\n")
        f.write("\\bottomrule\n")
        f.write("\\end{tabular}\n")
        f.write("\\end{table}\n")

    # 2. Tabela: Grade de Perda
    caminho_tab2 = os.path.join(pasta_saida, "tab_grade_perda.tex")
    with open(caminho_tab2, "w", encoding="utf-8") as f:
        f.write("% Tabela gerada automaticamente por analysis/analise.py\n")
        f.write("\\begin{table}[htbp]\n")
        f.write("\\caption{Desempenho Sob Perda de Mensagens ($N=5$, $p \\in [0, 0.3]$)}\n")
        f.write("\\label{tab:grade_perda}\n")
        f.write("\\centering\n")
        f.write("\\small\n")
        f.write("\\begin{tabular}{@{}cccccc@{}}\n")
        f.write("\\toprule\n")
        f.write("Perda ($p$) & Detecção (s) & Convergência (s) & Taxa Conv. & Falsos Posit. & Mensagens Bully \\\\\n")
        f.write(" & (Média $\\pm$ IC95\\%) & (Média $\\pm$ IC95\\%) & (\\%) & (par/min) & (Média $\\pm$ IC95\\%) \\\\\n")
        f.write("\\midrule\n")
        for p, r in grade_perda.items():
            det = fmt_media_ic(r["deteccao_max"])
            conv = fmt_media_ic(r["convergencia"]) if r["convergiram"] > 0 else "---"
            tx_conv = f"{r['taxa_convergencia_pct']:.0f}\\%"
            fp_par = f"{r['fp_por_par_min']['media']:.2f}"
            bully = fmt_media_ic(r["msgs_bully"], 1)
            f.write(f"{p:.1f} & ${det}$ & ${conv}$ & {tx_conv} & {fp_par} & ${bully}$ \\\\\n")
        f.write("\\bottomrule\n")
        f.write("\\end{tabular}\n")
        f.write("\\end{table}\n")

    # 3. Tabela: Cenários de Falha
    caminho_tab3 = os.path.join(pasta_saida, "tab_cenarios_falha.tex")
    with open(caminho_tab3, "w", encoding="utf-8") as f:
        f.write("% Tabela gerada automaticamente por analysis/analise.py\n")
        f.write("\\begin{table*}[htbp]\n")
        f.write("\\caption{Avaliação dos Cenários de Falhas Combinadas e Partições}\n")
        f.write("\\label{tab:cenarios_falha}\n")
        f.write("\\centering\n")
        f.write("\\small\n")
        f.write("\\begin{tabular}{@{}lcccc@{}}\n")
        f.write("\\toprule\n")
        f.write("Cenário & Eventos de Injeção & Split-Brain (s) & Líderes Máx. & Propriedades com Aviso \\\\\n")
        f.write(" & & (Média $\\pm$ IC95\\%) & & \\\\\n")
        f.write("\\midrule\n")
        nomes_legiveis = {
            "cascata_e_recuperacao": "Cascata e Recuperação",
            "particao_menor_id": "Partição Menor ID",
            "particao_queda_e_perda": "Partição, Queda e Perda",
            "particao_rede": "Partição de Rede (Líder)",
            "queda_e_recuperacao": "Queda e Recuperação",
        }
        for c, r in cenarios.items():
            nome = nomes_legiveis.get(c, c)
            eventos_resumo = ", ".join(r["eventos"][:4])
            if len(r["eventos"]) > 4:
                eventos_resumo += "..."
            eventos_resumo = eventos_resumo.replace("_", r"\_")
            sb = fmt_media_ic(r["dois_lideres_s"], 2)
            lmax = f"{int(r['lideres_max']['max'])}"
            avisos = ", ".join(r["avisos"]) if r["avisos"] else "Nenhum"
            f.write(f"{nome} & {eventos_resumo} & ${sb}$ & {lmax} & \\texttt{{{avisos}}} \\\\\n")
        f.write("\\bottomrule\n")
        f.write("\\end{tabular}\n")
        f.write("\\end{table*}\n")

    # 4. Tabela: Integração / Serviço de Tarefas
    caminho_tab4 = os.path.join(pasta_saida, "tab_integracao_servico.tex")
    with open(caminho_tab4, "w", encoding="utf-8") as f:
        f.write("% Tabela gerada automaticamente por analysis/analise.py\n")
        f.write("\\begin{table}[htbp]\n")
        f.write("\\caption{Desempenho e Disponibilidade do Serviço de Tarefas Integrado}\n")
        f.write("\\label{tab:integracao_servico}\n")
        f.write("\\centering\n")
        f.write("\\small\n")
        f.write("\\begin{tabular}{@{}lcccccc@{}}\n")
        f.write("\\toprule\n")
        f.write("Cenário & Vazão (req/s) & Latência p50 & Latência p95 & Latência p99 & Pausa Máx. & Tarefas 2x \\\\\n")
        f.write(" & (Média) & (ms) & (ms) & (ms) & (s) & (Média) \\\\\n")
        f.write("\\midrule\n")
        for c, r in integracao_sumario.items():
            nome = "Carga Normal" if "normal" in c else "Sob Quedas"
            vz = f"{r['vazao']['media']:.2f}"
            p50 = f"{r['p50_ms']['media']:.1f}"
            p95 = f"{r['p95_ms']['media']:.1f}"
            p99 = f"{r['p99_ms']['media']:.1f}"
            pausa = f"{r['pausa_s']['media']:.2f}"
            dup = f"{r['executadas_2x']['media']:.1f}"
            f.write(f"{nome} & {vz} & {p50} & {p95} & {p99} & {pausa} & {dup} \\\\\n")
        f.write("\\bottomrule\n")
        f.write("\\end{tabular}\n")
        f.write("\\end{table}\n")


# ==============================================================================
# 4. Geração de Gráficos e Figuras (PDF / PNG)
# ==============================================================================

def salvar_figura(fig, caminho_base):
    """Salva a figura em formato PDF (vetorial) e PNG (rasterizado)."""
    fig.savefig(caminho_base + ".pdf", bbox_inches="tight")
    fig.savefig(caminho_base + ".png", bbox_inches="tight")
    plt.close(fig)


def gerar_graficos(grade_nos, grade_perda, cenarios, integracao_tarefas, pasta_saida):
    """Gera o conjunto completo de figuras em PDF e PNG para o relatório IEEE."""
    os.makedirs(pasta_saida, exist_ok=True)

    # --------------------------------------------------------------------------
    # Figura 1: Complexidade de Mensagens do Bully e Heartbeat vs N
    # --------------------------------------------------------------------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 3.0))

    nos = sorted(grade_nos.keys())
    msgs_bully = [grade_nos[n]["msgs_bully"]["media"] for n in nos]
    msgs_ic = [grade_nos[n]["msgs_bully"]["ic95"] for n in nos]
    msgs_ele = [grade_nos[n]["msgs_election"]["media"] for n in nos]
    msgs_ok = [grade_nos[n]["msgs_ok"]["media"] for n in nos]
    msgs_coord = [grade_nos[n]["msgs_coordinator"]["media"] for n in nos]
    teorico_pior = [grade_nos[n]["teorico_bully_pior"] for n in nos]

    # Subplot 1: Mensagens Bully
    ax1.errorbar(nos, msgs_bully, yerr=msgs_ic, fmt="o-", color="#1f77b4", label="Total Observado (IC 95%)", capsize=4, lw=1.6)
    ax1.plot(nos, teorico_pior, "--", color="#d62728", label=r"Pior Caso Teórico: $N(N-1)$", lw=1.2)
    ax1.plot(nos, msgs_ele, ":s", color="#2ca02c", label=r"ELECTION", markersize=4)
    ax1.plot(nos, msgs_ok, ":^", color="#ff7f0e", label=r"OK", markersize=4)
    ax1.plot(nos, msgs_coord, ":v", color="#9467bd", label=r"COORDINATOR", markersize=4)
    ax1.set_xlabel("Número de Nós ($N$)")
    ax1.set_ylabel("Mensagens Trocadas")
    ax1.set_title("(a) Complexidade de Mensagens no Bully")
    ax1.set_xticks(nos)
    ax1.legend(frameon=True, facecolor="white", edgecolor="none")

    # Subplot 2: Heartbeats por segundo
    hb_obs = [grade_nos[n]["hb_msgs_s"]["media"] for n in nos]
    hb_teo = [grade_nos[n]["teorico_hb"] for n in nos]
    ax2.plot(nos, hb_obs, "o-", color="#1f77b4", label="Observado", lw=1.6)
    ax2.plot(nos, hb_teo, "--", color="#d62728", label=r"Teórico: $N(N-1)/\Delta t$", lw=1.2)
    ax2.set_xlabel("Número de Nós ($N$)")
    ax2.set_ylabel("Heartbeats por Segundo (req/s)")
    ax2.set_title("(b) Carga Contínua do Detector ($O(N^2)$)")
    ax2.set_xticks(nos)
    ax2.legend(frameon=True, facecolor="white", edgecolor="none")

    plt.tight_layout()
    salvar_figura(fig, os.path.join(pasta_saida, "fig1_complexidade_mensagens"))

    # --------------------------------------------------------------------------
    # Figura 2: Tempos de Detecção e Convergência vs N
    # --------------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    det_m = [grade_nos[n]["deteccao_max"]["media"] for n in nos]
    det_ic = [grade_nos[n]["deteccao_max"]["ic95"] for n in nos]
    conv_m = [grade_nos[n]["convergencia"]["media"] for n in nos]
    conv_ic = [grade_nos[n]["convergencia"]["ic95"] for n in nos]

    ax.errorbar(nos, det_m, yerr=det_ic, fmt="s-", color="#2ca02c", label="Detecção Máxima (s)", capsize=4, lw=1.5)
    ax.errorbar(nos, conv_m, yerr=conv_ic, fmt="o-", color="#1f77b4", label="Convergência Bully (s)", capsize=4, lw=1.5)
    ax.axhline(2.0, color="gray", linestyle="--", label=r"HB\_TIMEOUT (2.0s)", lw=1.0)
    ax.set_xlabel("Número de Nós ($N$)")
    ax.set_ylabel("Tempo Decorrido (s)")
    ax.set_title("Estabilidade Temporal sob Queda do Líder")
    ax.set_xticks(nos)
    ax.set_ylim(1.5, 3.0)
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="none")

    plt.tight_layout()
    salvar_figura(fig, os.path.join(pasta_saida, "fig2_tempos_convergencia_nos"))

    # --------------------------------------------------------------------------
    # Figura 3: Impacto da Perda de Pacotes (Convergência e Falsos Positivos)
    # --------------------------------------------------------------------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 3.0))
    perdas = sorted(grade_perda.keys())
    det_p = [grade_perda[p]["deteccao_max"]["media"] for p in perdas]
    conv_p = [grade_perda[p]["convergencia"]["media"] if grade_perda[p]["convergiram"] > 0 else np.nan for p in perdas]
    conv_ic_p = [grade_perda[p]["convergencia"]["ic95"] if grade_perda[p]["convergiram"] > 0 else np.nan for p in perdas]
    fp_taxa = [grade_perda[p]["fp_por_par_min"]["media"] for p in perdas]
    msgs_bully_p = [grade_perda[p]["msgs_bully"]["media"] for p in perdas]

    # Subplot 1: Detecção e Convergência vs Perda
    ax1.plot(perdas, det_p, "s-", color="#2ca02c", label="Detecção Máxima", lw=1.5)
    ax1.errorbar(perdas, conv_p, yerr=conv_ic_p, fmt="o-", color="#1f77b4", label="Convergência Bully", capsize=4, lw=1.5)
    ax1.set_xlabel("Probabilidade de Perda ($p$)")
    ax1.set_ylabel("Tempo Médio (s)")
    ax1.set_title("(a) Tempos de Estabilização sob Perda")
    ax1.set_xticks(perdas)
    ax1.legend(frameon=True, facecolor="white", edgecolor="none")

    # Subplot 2: Taxa de Falsos Positivos e Mensagens Espúrias
    color_fp = "#d62728"
    ax2.set_xlabel("Probabilidade de Perda ($p$)")
    ax2.set_ylabel("Falsos Positivos (por par-minuto)", color=color_fp)
    linha1 = ax2.plot(perdas, fp_taxa, "d-", color=color_fp, label="Taxa Falso Positivo", lw=1.6)
    ax2.tick_params(axis="y", labelcolor=color_fp)
    ax2.set_xticks(perdas)

    ax2_twin = ax2.twinx()
    color_msg = "#9467bd"
    ax2_twin.set_ylabel("Mensagens Bully", color=color_msg)
    linha2 = ax2_twin.plot(perdas, msgs_bully_p, "^--", color=color_msg, label="Mensagens Bully", lw=1.4)
    ax2_twin.tick_params(axis="y", labelcolor=color_msg)
    ax2_twin.grid(False)

    linhas = linha1 + linha2
    labels = [l.get_label() for l in linhas]
    ax2.legend(linhas, labels, loc="upper left", frameon=True, facecolor="white", edgecolor="none")
    ax2.set_title("(b) Degradação e Eleições Espúrias")

    plt.tight_layout()
    salvar_figura(fig, os.path.join(pasta_saida, "fig3_impacto_perda"))

    # --------------------------------------------------------------------------
    # Figura 4: Distribuição Acumulada de Latência (CDF) - Serviço de Tarefas
    # --------------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(4.5, 3.2))

    for c, lats in integracao_tarefas.items():
        arr = np.sort(lats)
        cdf = np.arange(1, len(arr) + 1) / len(arr)
        estilo = "-" if "normal" in c else "--"
        cor = "#1f77b4" if "normal" in c else "#d62728"
        label = "Carga Normal" if "normal" in c else "Sob Quedas de Nós"
        ax.plot(arr, cdf, estilo, color=cor, label=label, lw=1.8)

    ax.axhline(0.50, color="gray", linestyle=":", lw=0.9, label="p50, p95, p99")
    ax.axhline(0.95, color="gray", linestyle=":", lw=0.9)
    ax.axhline(0.99, color="gray", linestyle=":", lw=0.9)

    ax.set_xlabel("Latência da Tarefa (ms)")
    ax.set_ylabel("Probabilidade Acumulada (CDF)")
    ax.set_title("CDF de Latência do Serviço de Tarefas")
    ax.set_xlim(80, 2200)
    ax.legend(loc="lower right", frameon=True, facecolor="white", edgecolor="none")

    plt.tight_layout()
    salvar_figura(fig, os.path.join(pasta_saida, "fig4_integracao_latencia_cdf"))

    # --------------------------------------------------------------------------
    # Figura 5: Duração de Split-Brain nos Cenários de Falha e Partição
    # --------------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(4.8, 3.2))
    cen_chaves = [
        "queda_e_recuperacao",
        "cascata_e_recuperacao",
        "particao_rede",
        "particao_menor_id",
        "particao_queda_e_perda"
    ]
    nomes_graf = ["Queda e Rec.", "Cascata", "Partição Líder", "Partição Menor", "Part.+Queda+Perda"]
    duracoes = [cenarios[c]["dois_lideres_s"]["media"] for c in cen_chaves]
    erros = [cenarios[c]["dois_lideres_s"]["ic95"] for c in cen_chaves]

    cores = ["#2ca02c" if d < 0.1 else "#ff7f0e" if d < 5.0 else "#d62728" for d in duracoes]
    barras = ax.bar(nomes_graf, duracoes, yerr=erros, color=cores, capsize=4, edgecolor="black", lw=0.8, alpha=0.85)

    for bar, d in zip(barras, duracoes):
        altura = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2.0, altura + 0.25, f"{d:.2f}s", ha="center", va="bottom", fontsize=8)

    ax.set_ylabel("Tempo com > 1 Líder (s)")
    ax.set_title("Duração de Split-Brain por Cenário")
    ax.set_ylim(0, 9.5)
    plt.xticks(rotation=20, ha="right")

    plt.tight_layout()
    salvar_figura(fig, os.path.join(pasta_saida, "fig5_cenarios_split_brain"))


# ==============================================================================
# 5. Impressão de Sumário no Terminal e Função Principal
# ==============================================================================

def imprimir_sumario(grade_nos, grade_perda, cenarios, integracao_sumario):
    """Exibe no stdout um resumo legível e formatado de todos os resultados."""
    print("=" * 78)
    print(" CONSOLIDAÇÃO ESTATÍSTICA DOS EXPERIMENTOS (MC714 - PROJETO 02)")
    print("=" * 78)

    print("\n1. GRADE DE NÓS (Variação de N, Sem Perda):")
    print(f"{'N':<3} | {'Detecção Máx (s)':<18} | {'Convergência (s)':<18} | {'Msgs Bully':<14} | {'Heartbeats/s':<12}")
    print("-" * 78)
    for n, r in grade_nos.items():
        det = fmt_media_ic(r["deteccao_max"])
        conv = fmt_media_ic(r["convergencia"])
        bully = fmt_media_ic(r["msgs_bully"], 1)
        hb = f"{r['hb_msgs_s']['media']:.1f}"
        print(f"{n:<3} | {det:<18} | {conv:<18} | {bully:<14} | {hb:<12}")

    print("\n2. GRADE DE PERDA (N = 5, Variação de drop_prob):")
    print(f"{'Perda':<5} | {'Detecção Máx (s)':<18} | {'Convergência (s)':<18} | {'Conv %':<6} | {'FP (par/min)':<12}")
    print("-" * 78)
    for p, r in grade_perda.items():
        det = fmt_media_ic(r["deteccao_max"])
        conv = fmt_media_ic(r["convergencia"]) if r["convergiram"] > 0 else "---"
        tx_conv = f"{r['taxa_convergencia_pct']:.0f}%"
        fp_par = f"{r['fp_por_par_min']['media']:.2f}"
        print(f"{p:<5.1f} | {det:<18} | {conv:<18} | {tx_conv:<6} | {fp_par:<12}")

    print("\n3. CENÁRIOS DE FALHA E PARTIÇÃO:")
    print(f"{'Cenário':<24} | {'Split-Brain (s)':<18} | {'Líderes Máx':<11} | {'Avisos Verificador'}")
    print("-" * 78)
    for c, r in cenarios.items():
        sb = fmt_media_ic(r["dois_lideres_s"], 2)
        lmax = f"{int(r['lideres_max']['max'])}"
        avisos = ", ".join(r["avisos"]) if r["avisos"] else "Nenhum"
        print(f"{c:<24} | {sb:<18} | {lmax:<11} | {avisos}")

    print("\n4. SERVIÇO DE TAREFAS INTEGRADO:")
    print(f"{'Cenário':<20} | {'Vazão (req/s)':<13} | {'p50 (ms)':<9} | {'p95 (ms)':<9} | {'p99 (ms)':<9} | {'Pausa Máx (s)'}")
    print("-" * 78)
    for c, r in integracao_sumario.items():
        vz = f"{r['vazao']['media']:.2f}"
        p50 = f"{r['p50_ms']['media']:.1f}"
        p95 = f"{r['p95_ms']['media']:.1f}"
        p99 = f"{r['p99_ms']['media']:.1f}"
        pausa = f"{r['pausa_s']['media']:.2f}"
        print(f"{c:<20} | {vz:<13} | {p50:<9} | {p95:<9} | {p99:<9} | {pausa}")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(description="Consolidação estatística dos experimentos (Issue #9).")
    parser.add_argument("--dados", default="results/experimentos", help="Diretório dos CSVs brutos.")
    parser.add_argument("--saida", default="results/analise", help="Diretório base de saída.")
    parser.add_argument("--sem-graficos", action="store_true", help="Gera apenas tabelas e estatísticas.")
    parser.add_argument("--quiet", action="store_true", help="Não imprime o resumo no terminal.")
    args = parser.parse_args()

    dados_dir = args.dados
    saida_dir = args.saida
    tabelas_dir = os.path.join(saida_dir, "tabelas")
    figuras_dir = os.path.join(saida_dir, "figuras")

    # 1. Carregamento dos dados
    linhas_nos = carregar_csv(os.path.join(dados_dir, "nos.csv"))
    linhas_perda = carregar_csv(os.path.join(dados_dir, "perda.csv"))
    linhas_cenarios = carregar_csv(os.path.join(dados_dir, "cenarios.csv"))
    linhas_integracao = carregar_csv(os.path.join(dados_dir, "integracao.csv"))
    linhas_tarefas = carregar_csv(os.path.join(dados_dir, "integracao_tarefas.csv"))

    if not (linhas_nos and linhas_perda and linhas_cenarios and linhas_integracao):
        print(f"[ERRO] Arquivos CSV não encontrados em '{dados_dir}'. Execute antes experiments.run_all.",
              file=sys.stderr)
        sys.exit(1)

    # 2. Processamento estatístico
    grade_nos = analisar_grade_nos(linhas_nos)
    grade_perda = analisar_grade_perda(linhas_perda)
    cenarios = analisar_cenarios(linhas_cenarios)
    integracao_sumario, integracao_tarefas = analisar_integracao(linhas_integracao, linhas_tarefas)

    # 3. Geração das tabelas LaTeX
    gerar_tabelas_latex(grade_nos, grade_perda, cenarios, integracao_sumario, tabelas_dir)

    # 4. Geração dos gráficos
    if not args.sem_graficos:
        gerar_graficos(grade_nos, grade_perda, cenarios, integracao_tarefas, figuras_dir)

    # 5. Sumário no stdout
    if not args.quiet:
        imprimir_sumario(grade_nos, grade_perda, cenarios, integracao_sumario)

    print(f"\n[OK] Análise concluída com sucesso!")
    print(f" -> Tabelas LaTeX salvas em: {tabelas_dir}")
    if not args.sem_graficos:
        print(f" -> Figuras (PDF e PNG) salvas em: {figuras_dir}")


if __name__ == "__main__":
    main()
