#!/usr/bin/env bash
# Запуск «Книжного стража». Все аргументы передаются программе:
#   bash run.sh            — обычный запуск
#   bash run.sh demo       — демо без микрофона и админа
#   bash run.sh simulate   — проверка логики в консоли
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python run.py "$@"
else
  exec python3 run.py "$@"
fi
