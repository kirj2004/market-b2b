#!/usr/bin/env bash
# Запуск кабинета клиента MARKET.
#
#   ./run.sh init     — создать схему БД и администратора
#   ./run.sh dev      — локальный отладочный запуск на 127.0.0.1:5000
#   ./run.sh serve    — прод: gunicorn на 127.0.0.1:5000 (его проксирует nginx)
#   ./run.sh stats    — сводка по заявкам
#   ./run.sh token user@example.ru — выпустить API-токен
#
# Переменные окружения читает config.py. Секреты можно задать здесь или
# в файле .env рядом (см. .env.example).

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
PY="$VENV/bin/python"
GUNICORN="$VENV/bin/gunicorn"

cd "$DIR"

# .env в git не лежит; читаем, если есть.
if [[ -f "$DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$DIR/.env"
  set +a
fi

if [[ ! -x "$PY" ]]; then
  echo "Нет виртуального окружения. Создаём:" >&2
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$DIR/requirements.txt"
fi

command="${1:-dev}"
shift || true

case "$command" in
  init)
    exec "$PY" -m flask --app 'app:create_app()' init-db
    ;;
  dev)
    export FLASK_DEBUG=1
    export MARKET_DEBUG=1
    exec "$PY" app.py
    ;;
  serve)
    workers="${MARKET_WORKERS:-2}"
    timeout="${MARKET_TIMEOUT:-120}"
    # 1 worker на 2 ядра — SQLite плохо переносит конкурентную запись,
    # а gunicorn с sync-воркерами держит запись короткой.
    exec "$GUNICORN" \
      --workers "$workers" \
      --threads 4 \
      --timeout "$timeout" \
      --bind "127.0.0.1:${MARKET_PORT:-5000}" \
      --access-logfile - \
      --error-logfile - \
      --capture-output \
      'app:create_app()'
    ;;
  stats)
    exec "$PY" -m flask --app 'app:create_app()' stats
    ;;
  token)
    exec "$PY" -m flask --app 'app:create_app()' token --user "$*"
    ;;
  *)
    echo "Неизвестная команда: $command" >&2
    echo "Доступно: init | dev | serve | stats | token <email>" >&2
    exit 2
    ;;
esac