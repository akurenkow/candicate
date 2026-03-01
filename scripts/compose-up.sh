#!/usr/bin/env sh
# Запуск docker-compose с авто-выбором: vendor (без ollama) или local (с ollama).
# При валидном API ключе — не стартует ollama. При невалидном — fallback на ollama.
# Использование:
#   ./scripts/compose-up.sh [-d] [--gpu]
#   -d: detached (фон)
#   --gpu: добавить docker-compose.gpu.yml

set -e
cd "$(dirname "$0")/.."

DETACHED=""
COMPOSE_FILES="-f docker-compose.yml"
GPU=""
while [ $# -gt 0 ]; do
  case "$1" in
    -d) DETACHED="-d"; shift ;;
    --gpu) GPU=1; shift ;;
    *) shift ;;
  esac
done

[ -n "$GPU" ] && COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.gpu.yml"

echo ">>> Проверка LLM API (scripts/check_llm_vendor.py)..."
if python scripts/check_llm_vendor.py 2>/dev/null; then
  echo ">>> Vendor API OK — запуск без ollama, с lightweight-embeddings"
  COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.vendor-embeddings.yml"
  exec docker compose $COMPOSE_FILES up $DETACHED --remove-orphans
else
  echo ">>> Fallback на Ollama — добавление docker-compose.ollama.yml"
  COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.ollama.yml"
  [ -n "$GPU" ] && COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.ollama-gpu.yml"
  exec docker compose $COMPOSE_FILES up $DETACHED --remove-orphans
fi
