# Porta de entrada única dos experimentos (issue #6): roda todas as grades e grava os
# CSVs brutos e os metadados em results/experimentos/.
#
# Uso (da raiz do repositório; só precisa da biblioteca padrão do Python):
#   python -m experiments.run_all                     # todas as grades, 10 repetições (~23 min)
#   python -m experiments.run_all --grades nos --repeticoes 2
# Os logs de cada ensaio ficam em results/raw/experimentos/<grade>/ (fora do git).
# O que cada grade mede e como: docs/experimentos.md.
import argparse
import signal
import sys

from experiments.grid import GridConfig, execute_grid

GRADES = [
    # custo do detector (O(n²) heartbeats) e do Bully (O(n²) mensagens no pior caso) com n
    GridConfig("nos", "n", [3, 5, 7, 9]),
    # falsos positivos do detector e convergência do Bully com perda de mensagens
    GridConfig("perda", "drop_prob", [0.0, 0.1, 0.2, 0.3], base={"n": 5}),
]


def main(argv=None):
    p = argparse.ArgumentParser(description="Roda as grades de experimentos e grava os CSVs brutos")
    p.add_argument("--grades", nargs="+", choices=[g.nome for g in GRADES],
                   default=[g.nome for g in GRADES])
    p.add_argument("--repeticoes", type=int, default=None, help="padrão: o de cada grade (10)")
    p.add_argument("--saida", default="results/experimentos")
    args = p.parse_args(argv)
    # SIGTERM (kill) vira SystemExit: os ensaios param os nós e gravam o que já rodou
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    for g in GRADES:
        if g.nome in args.grades:
            if args.repeticoes is not None:
                g.repeticoes = args.repeticoes
            r = execute_grid(g, saida=args.saida)
            print(f"[{g.nome}] {len(r.linhas)} ensaios -> {r.csv} (+ .meta.json)")


if __name__ == "__main__":
    main()
