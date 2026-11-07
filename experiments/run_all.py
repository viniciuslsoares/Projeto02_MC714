# Porta de entrada única dos experimentos (issues #6 e #7): roda todas as grades e os cenários
# de falha e grava os CSVs brutos e os metadados em results/experimentos/.
#
# Uso (da raiz do repositório; precisa só da biblioteca padrão e do pyyaml, para os cenários):
#   python -m experiments.run_all                     # tudo: grades com 10 repetições, cenários com 5 (~33 min)
#   python -m experiments.run_all --grades nos --repeticoes 2
#   python -m experiments.run_all --grades cenarios   # só os cenários (~9 min)
# Os logs de cada ensaio ficam em results/raw/ (fora do git).
# O que cada grade mede e como: docs/experimentos.md; os cenários: docs/cenarios.md.
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
    nomes = [g.nome for g in GRADES] + ["cenarios"]
    p.add_argument("--grades", nargs="+", choices=nomes, default=nomes)
    p.add_argument("--repeticoes", type=int, default=None,
                   help="padrão: 10 em cada grade e 5 nos cenários")
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
    if "cenarios" in args.grades:
        from experiments import cenarios   # aqui: só os cenários precisam do pyyaml
        linhas = cenarios.executar(repeticoes=args.repeticoes or 5, saida=args.saida)
        print(f"[cenarios] {len(linhas)} execuções -> {args.saida}/cenarios.csv (+ .meta.json)")


if __name__ == "__main__":
    main()
