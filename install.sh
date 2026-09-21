#!/usr/bin/env bash
# ============================================================
#  УСТАНОВЩИК «КНИЖНОГО СТРАЖА» (Linux / macOS)
#  Запуск:  bash install.sh
# ============================================================
cd "$(dirname "$0")"
set -u

echo "============================================================"
echo "  УСТАНОВКА «КНИЖНОГО СТРАЖА»"
echo "============================================================"
echo

echo "--- 1/6 Проверяю Python ---"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[x] Нужен Python 3. Установите: sudo apt install python3 python3-venv"
  exit 1
fi
echo "[ok] Python: $(python3 --version)"

# звук (PortAudio) — для работы микрофона
if [ "$(uname)" = "Linux" ]; then
  if ! ldconfig -p 2>/dev/null | grep -q portaudio; then
    echo "Устанавливаю libportaudio2 (нужны sudo)..."
    sudo apt-get install -y libportaudio2 python3-tk 2>/dev/null \
      || sudo dnf install -y portaudio python3-tkinter 2>/dev/null \
      || echo "[!] Не удалось установить автоматически. Установите libportaudio2 и python3-tk сами."
  fi
fi

echo "--- 2/6 Виртуальное окружение и зависимости ---"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
PY=.venv/bin/python
$PY -m pip install --upgrade pip -q
$PY -m pip install -q vosk sounddevice
if [ $? -ne 0 ]; then
  echo "[x] Не удалось установить vosk/sounddevice (нужен интернет)."
  exit 1
fi
echo "[ok] vosk + sounddevice установлены."

echo "--- 3/6 Модель распознавания речи (офлайн, русский) ---"
mkdir -p models
if [ ! -d models/vosk-model-small-ru ] && [ ! -d models/vosk-model-ru-big ]; then
  echo
  echo "  1) Быстрая модель            ~33 МБ   (для слабых компьютеров)"
  echo "  2) Максимальная точность     ~1.4 ГБ  (минимум ошибок в словах)"
  echo
  read -r -p "Выберите (1 или 2, Enter=1): " CHOOSE
  CHOOSE=${CHOOSE:-1}
else
  CHOOSE=1
  [ -d models/vosk-model-ru-big ] && CHOOSE=2
fi

if [ "$CHOOSE" = "2" ]; then
  $PY -m bookguard model big
  if [ ! -d models/vosk-model-ru-big ] || [ -z "$(ls -A models/vosk-model-ru-big 2>/dev/null)" ]; then
    echo "Скачиваю модель «максимальная точность» (~1.4 ГБ, это долго)..."
    $PY - <<'EOF'
import sys
from bookguard import stt
try:
    stt.download_model("big", progress_cb=lambda d, t: print(
        f"\r  {d/1048576:.0f} / {t/1048576:.0f} МБ", end="", flush=True) if t else None,
        log=lambda s: print("\n" + s))
    print("\n[ok] модель «максимальная точность» установлена.")
except Exception as e:
    print(f"\n[x] Не удалось скачать большую модель ({e}). Установится быстрая.")
    sys.exit(3)
EOF
    if [ $? -eq 3 ]; then $PY -m bookguard model fast; fi
  else
    echo "[ok] модель «максимальная точность» уже установлена."
  fi
else
  $PY -m bookguard model fast
  if [ ! -d models/vosk-model-small-ru ] || [ -z "$(ls -A models/vosk-model-small-ru 2>/dev/null)" ]; then
    echo "Скачиваю быструю модель (~33 МБ)..."
    $PY - <<'EOF'
from bookguard import stt
try:
    stt.download_model("fast", progress_cb=lambda d, t: print(
        f"\r  {d/1048576:.0f} / {t/1048576:.0f} МБ", end="", flush=True) if t else None,
        log=lambda s: print("\n" + s))
    print("\n[ok] быстрая модель установлена.")
except Exception as e:
    print(f"\n[x] Не удалось скачать модель: {e}")
    raise SystemExit(1)
EOF
    [ $? -eq 0 ] || exit 1
  else
    echo "[ok] быстрая модель уже установлена."
  fi
fi

echo "--- 4/6 База данных и самопроверка ---"
if [ ! -f data/library.db ]; then
  echo "[x] В комплекте должна быть data/library.db. Сообщите разработчику."
  exit 1
fi
$PY -m bookguard selftest
if [ $? -ne 0 ]; then
  echo "[x] Самопроверка не прошла. Подробности выше."
  exit 1
fi
echo "[ok] Самопроверка пройдена."

echo "--- 5/6 Ярлык на рабочий стол ---"
DESK="$HOME/Desktop"
[ -d "$HOME/Рабочий стол" ] && DESK="$HOME/Рабочий стол"
if [ -d "$DESK" ]; then
  cat > "$DESK/Книжный страж.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Книжный страж
Comment=Читай книгу вслух — открывай Wi-Fi
Exec=bash -c "cd '$(pwd)' && bash run.sh"
Path=$(pwd)
Icon=accessories-calculator
Terminal=false
EOF
  chmod +x "$DESK/Книжный страж.desktop" 2>/dev/null
  echo "[ok] Ярлык «Книжный страж» на рабочем столе ($DESK)."
else
  echo "[i] Рабочий стол не найден — ярлык не создаётся (запуск: bash run.sh)."
fi

echo "--- 6/6 Автозапуск и защита от удаления ---"
# безпарольные права на управление Wi-Fi (чтобы работало при автозапуске)
if command -v sudo >/dev/null 2>&1 && [ -d /etc/sudoers.d ]; then
  NMCLI=$(command -v nmcli || echo /usr/bin/nmcli)
  RFKILL=$(command -v rfkill || echo /usr/sbin/rfkill)
  echo "$USER ALL=(root) NOPASSWD: $NMCLI, $RFKILL" | sudo tee /etc/sudoers.d/bookguard >/dev/null 2>&1 || true
  sudo chmod 440 /etc/sudoers.d/bookguard 2>/dev/null || true
fi
$PY -m bookguard autostart on
if [ $? -eq 0 ]; then
  echo "[ok] Автозапуск: при входе в систему программа стартует сама."
else
  echo "[!] Не удалось включить автозапуск — повторите: bash run.sh autostart on"
fi
$PY -m bookguard guard-setup
echo "[ok] Защита от удаления: программа не удаляется без пароля 67676767."

echo
echo "============================================================"
echo "  УСТАНОВКА ЗАВЕРШЕНА"
echo "============================================================"
echo " * Программа стартует сама при включении компьютера и блокирует Wi-Fi."
echo " * Удаление программы: bash uninstall.sh  (нужен пароль 67676767)."
echo " * Запуск вручную: bash run.sh      Демо без микрофона: bash run.sh demo"
echo "============================================================"
