#!/usr/bin/env bash
# Executa um cenário declarativo de falhas com o cluster Docker e salva os logs em results/raw/
#
# Uso:
#   ./scripts/run_scenario.sh scenarios/particao_rede.yaml [seed]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ $# -lt 1 ]; then
  echo "Uso: $0 <caminho_do_cenario.yaml> [seed]"
  exit 1
fi

SCENARIO_FILE="$1"
if [ ! -f "$SCENARIO_FILE" ]; then
  echo "Erro: arquivo '$SCENARIO_FILE' não encontrado."
  exit 1
fi

# Extrai nome e seed do cenário usando Python
SCENARIO_NAME=$(python -c "import yaml; d=yaml.safe_load(open('$SCENARIO_FILE')); print(d.get('name', 'cenario'))")
YAML_SEED=$(python -c "import yaml; d=yaml.safe_load(open('$SCENARIO_FILE')); print(d.get('seed', 1001))")
SEED="${2:-$YAML_SEED}"

DEST_DIR="results/raw/${SCENARIO_NAME}_seed${SEED}"
echo "=================================================="
echo "Executando Cenário: $SCENARIO_NAME (seed=$SEED)"
echo "Destino dos logs:  $DEST_DIR"
echo "=================================================="

# 1. Prepara diretórios
mkdir -p "$DEST_DIR"
rm -f "$DEST_DIR"/node*.jsonl   # logs de uma execução anterior não se misturam aos desta
mkdir -p results/logs
rm -f results/logs/*.jsonl
rm -rf results/logs/state

# 2. Sobe o cluster Docker
echo ">> Subindo cluster de contêineres..."
SEED="$SEED" docker compose up -d --build --wait
sleep 2

# 3. Executa a injeção programada de falhas
echo ">> Disparando injetor de falhas..."
python -m experiments.fault_injector "$SCENARIO_FILE"

# 4. Salva logs gerados e metadados de execução
echo ">> Coletando logs em $DEST_DIR..."
cp -r results/logs/* "$DEST_DIR/" 2>/dev/null || true

GIT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "desconhecido")
cat <<EOF > "$DEST_DIR/meta.json"
{
  "scenario": "$SCENARIO_NAME",
  "scenario_file": "$SCENARIO_FILE",
  "seed": $SEED,
  "git_commit": "$GIT_COMMIT",
  "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date)"
}
EOF

# 5. Derruba o cluster
echo ">> Finalizando contêineres..."
docker compose down -v >/dev/null 2>&1

# 6. Verifica segurança e vivacidade nos logs coletados (docs/testes.md)
echo ">> Verificando os logs..."
python -m analysis.log_verifier "$DEST_DIR" --saida "$DEST_DIR/verifications.txt"

echo "=================================================="
echo "[OK] Cenário concluído com sucesso!"
echo "Logs salvos em: $DEST_DIR"
echo "=================================================="
