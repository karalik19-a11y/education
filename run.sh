#!/usr/bin/env bash
# ============================================================
#  Запуск «Книжного стража» (Linux / macOS).  bash run.sh [аргументы]
#  * до 3 попыток запуска с логом (data/startup.log);
#  * фоновый «страж» (защита от удаления) запускается сам.
#  bash run.sh guard   — режим фоновой защиты (используется автозапуском)
# ============================================================
cd "$(dirname "$0")"
mkdir -p data

if [ ! -x .venv/bin/python ]; then
  PY=python3
else
  PY=.venv/bin/python
fi

if [ "$1" = "guard" ]; then
  # отдельный режим: только фоновый страж (для автозагрузки)
  exec "$PY" -m bookguard guard >> data/guard.out 2>&1
fi

# ---------- фоновый страж (один экземпляр) ----------
nohup "$PY" -m bookguard guard >> data/guard.out 2>&1 &

# ---------- самочиняющийся запуск ----------
i=0
while true; do
  i=$((i+1))
  if [ "$i" -gt 1 ]; then
    echo
    echo "Не удалось запустить (попытка $i/3). Повтор через 2 секунды..."
    echo "Подробности: data/startup.log"
    sleep 2
  fi
  "$PY" run.py "$@" 2>> data/startup.log
  rc=$?
  [ $rc -eq 0 ] && exit 0
  [ $i -ge 3 ] && break
done

echo
echo "============================================================"
echo "  НЕ УДАЛОСЬ ЗАПУСТИТЬ ПРОГРАММУ"
echo "  См. data/startup.log и data/app.log"
echo "  Для восстановления запустите:  bash install.sh"
echo "============================================================"
exit 1
