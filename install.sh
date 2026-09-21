#!/usr/bin/env bash
# Установка «Книжного стража» (Linux / macOS).
# Запуск:  bash install.sh
set -e
cd "$(dirname "$0")"

echo "=== Установка «Книжный страж» ==="

if ! command -v python3 >/dev/null 2>&1; then
  echo "Нужен Python 3. Установите его (sudo apt install python3 python3-venv python3-tk)."
  exit 1
fi

# звук (PortAudio) — для работы микрофона на Linux
if [ "$(uname)" = "Linux" ]; then
  if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
    echo "Пытаюсь установить libportaudio2 (нужны права sudo)..."
    sudo apt-get install -y libportaudio2 python3-tk 2>/dev/null \
      || sudo dnf install -y portaudio python3-tkinter 2>/dev/null \
      || echo "Не удалось установить автоматически. Установите пакеты libportaudio2 и python3-tk сами."
  fi
fi

echo "--- 1/4 Виртуальное окружение..."
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
PY=.venv/bin/python
$PY -m pip install --upgrade pip -q
$PY -m pip install -q vosk sounddevice

echo "--- 2/4 Модель распознавания речи (русский, офлайн)..."
mkdir -p models
if [ ! -d models/vosk-model-small-ru ] || [ -z "$(ls -A models/vosk-model-small-ru 2>/dev/null)" ]; then
  OK=0
  if command -v curl >/dev/null 2>&1; then
    echo "Пробую загрузить с alphacephei.com..."
    if curl -L --fail -m 300 -o /tmp/vosk-ru.zip \
        https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip; then
      unzip -q -o /tmp/vosk-ru.zip -d models && rm -f /tmp/vosk-ru.zip
      mv models/vosk-model-small-ru-0.22 models/vosk-model-small-ru 2>/dev/null || true
      OK=1
    fi
  fi
  if [ "$OK" != "1" ]; then
    echo "Пробую зеркало с GitHub..."
    rm -rf /tmp/klara-mirror
    git clone --depth 1 --filter=blob:none --sparse https://github.com/s1reeen/Klara.git /tmp/klara-mirror
    ( cd /tmp/klara-mirror && git sparse-checkout set vosk-model-small-ru )
    cp -r /tmp/klara-mirror/vosk-model-small-ru models/
    rm -rf /tmp/klara-mirror
  fi
fi
echo "Модель готова: models/vosk-model-small-ru"

echo "--- 3/4 База данных библиотеки..."
if [ ! -f data/library.db ]; then
  if [ -d .downloads/RusLit ]; then
    $PY tools/build_library.py
  else
    echo "Исходные тексты недоступны, но data/library.db должен лежать в комплекте."
  fi
fi

echo "--- 4/5 Самопроверка..."
$PY -m bookguard selftest

echo "--- 5/5 Автозапуск при включении компьютера..."
# безпарольные права на управление Wi-Fi (чтобы работало при автозапуске)
if command -v sudo >/dev/null 2>&1 && [ -d /etc/sudoers.d ]; then
  NMCLI=$(command -v nmcli || echo /usr/bin/nmcli)
  RFKILL=$(command -v rfkill || echo /usr/sbin/rfkill)
  echo "$USER ALL=(root) NOPASSWD: $NMCLI, $RFKILL" | sudo tee /etc/sudoers.d/bookguard >/dev/null 2>&1 || true
  sudo chmod 440 /etc/sudoers.d/bookguard 2>/dev/null || true
fi
$PY -m bookguard autostart on

echo
echo "=== Установка завершена! ==="
echo "«Книжный страж» добавлен в автозагрузку и будет сам запускаться при входе в систему."
echo "(Отключить:  bash run.sh autostart off)"
echo "Запуск вручную: bash run.sh          Демо без микрофона: bash run.sh demo"
