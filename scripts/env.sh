#!/usr/bin/env bash
# Ciclo de vida do ambiente (Docker Compose v2). Uso: ./scripts/env.sh {build|up|up5|ps|logs|down|smoke}
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

case "${1:-}" in
  build) docker compose build ;;
  up)    mkdir -p results/logs && docker compose up -d --wait ;;
  up5)   mkdir -p results/logs && PEERS="1=node1:8000,2=node2:8000,3=node3:8000,4=node4:8000,5=node5:8000" docker compose --profile n5 up -d --wait ;;
  ps)    docker compose --profile n5 ps ;;
  logs)  docker compose --profile n5 logs --no-color "${@:2}" ;;
  down)  docker compose --profile n5 down -v ;;
  smoke) # sobe 3 nós, espera pings entre todos e derruba
    mkdir -p results/logs
    docker compose up -d --build --wait
    sleep 4
    n=$(docker compose logs --no-color 2>/dev/null | grep -c '"event": "reply"' || true)
    docker compose down -v
    echo "replies de ping observadas: $n"
    [ "$n" -ge 6 ] && echo "SMOKE OK" || { echo "SMOKE FALHOU"; exit 1; } ;;
  *) echo "uso: $0 {build|up|up5|ps|logs|down|smoke}"; exit 2 ;;
esac

