"""Testes do script de consolidação estatística (analysis/analise.py, Issue #9)."""
import os
import tempfile
import pytest
import numpy as np

from analysis.analise import (
    estatisticas,
    to_float,
    fmt_media_ic,
    fmt_p50_p95,
    carregar_csv,
    analisar_grade_nos,
    analisar_grade_perda,
    analisar_cenarios,
    analisar_integracao,
    gerar_tabelas_latex,
    gerar_graficos,
)


def test_estatisticas_basicas():
    amostras = [10.0, 12.0, 14.0, 16.0, 18.0]
    res = estatisticas(amostras)
    assert res["n"] == 5
    assert res["media"] == 14.0
    assert pytest.approx(res["std"], 0.01) == float(np.std(amostras, ddof=1))
    assert res["ic95"] > 0
    assert res["p50"] == 14.0
    assert res["min"] == 10.0
    assert res["max"] == 18.0


def test_estatisticas_vazias_e_unitarias():
    vazio = estatisticas([])
    assert vazio["n"] == 0 and vazio["media"] == 0.0 and vazio["ic95"] == 0.0

    um = estatisticas([5.0])
    assert um["n"] == 1 and um["media"] == 5.0 and um["std"] == 0.0 and um["ic95"] == 0.0


def test_formatadores():
    stats = {"media": 10.556, "ic95": 0.333, "p50": 10.5, "p95": 11.2}
    assert fmt_media_ic(stats, 2) == "10.56 \\pm 0.33"
    assert fmt_p50_p95(stats, 1) == "10.5 / 11.2"
    assert to_float("42.5") == 42.5
    assert to_float("", padrao=0.0) == 0.0
    assert to_float(None, padrao=-1.0) == -1.0


def test_execucao_completa_com_dados_reais(tmp_path):
    dados_dir = "results/experimentos"
    linhas_nos = carregar_csv(os.path.join(dados_dir, "nos.csv"))
    linhas_perda = carregar_csv(os.path.join(dados_dir, "perda.csv"))
    linhas_cenarios = carregar_csv(os.path.join(dados_dir, "cenarios.csv"))
    linhas_integracao = carregar_csv(os.path.join(dados_dir, "integracao.csv"))
    linhas_tarefas = carregar_csv(os.path.join(dados_dir, "integracao_tarefas.csv"))

    assert len(linhas_nos) > 0
    assert len(linhas_perda) > 0
    assert len(linhas_cenarios) > 0
    assert len(linhas_integracao) > 0
    assert len(linhas_tarefas) > 0

    grade_nos = analisar_grade_nos(linhas_nos)
    grade_perda = analisar_grade_perda(linhas_perda)
    cenarios = analisar_cenarios(linhas_cenarios)
    integracao_sumario, integracao_tarefas = analisar_integracao(linhas_integracao, linhas_tarefas)

    assert set(grade_nos.keys()) == {3, 5, 7, 9}
    assert 0.0 in grade_perda
    assert "cascata_e_recuperacao" in cenarios
    assert "integracao_normal" in integracao_sumario

    tabelas_dir = str(tmp_path / "tabelas")
    figuras_dir = str(tmp_path / "figuras")

    gerar_tabelas_latex(grade_nos, grade_perda, cenarios, integracao_sumario, tabelas_dir)
    assert os.path.exists(os.path.join(tabelas_dir, "tab_grade_nos.tex"))
    assert os.path.exists(os.path.join(tabelas_dir, "tab_grade_perda.tex"))
    assert os.path.exists(os.path.join(tabelas_dir, "tab_cenarios_falha.tex"))
    assert os.path.exists(os.path.join(tabelas_dir, "tab_integracao_servico.tex"))

    gerar_graficos(grade_nos, grade_perda, cenarios, integracao_tarefas, figuras_dir)
    for fig_id in (
        "fig1_complexidade_mensagens",
        "fig2_tempos_convergencia_nos",
        "fig3_impacto_perda",
        "fig4_integracao_latencia_cdf",
        "fig5_cenarios_split_brain",
    ):
        assert os.path.exists(os.path.join(figuras_dir, f"{fig_id}.pdf"))
        assert os.path.exists(os.path.join(figuras_dir, f"{fig_id}.png"))
