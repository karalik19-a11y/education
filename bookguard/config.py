# -*- coding: utf-8 -*-
"""Настройки «Книжного стража»."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "library.db"
MODELS_DIR = ROOT / "models"
VOSK_MODEL_FAST_DIR = MODELS_DIR / "vosk-model-small-ru"
VOSK_MODEL_BIG_DIR = MODELS_DIR / "vosk-model-ru-big"

# URL моделей (офлайн-распознавание Vosk, русский)
VOSK_MODEL_FAST_URL = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"
VOSK_MODEL_BIG_URL = "https://alphacephei.com/vosk/models/vosk-model-ru-0.42.zip"
# Зеркало (только для быстрой модели)
VOSK_MODEL_FAST_MIRROR = "https://codeload.github.com/s1reeen/Klara/zip/refs/heads/main"

# Сколько страниц нужно прочитать (можно менять в БД, таблица state)
DEFAULT_PAGES_REQUIRED = 50
# 1 «страница» = 1000 знаков чистого текста (см. tools/build_library.py)
PAGE_CHARS = 1000

# Экстренный пароль (включение Wi-Fi / удаление программы)
EMERGENCY_PASSWORD = "67676767"

# Тест: 5 вопросов, для зачёта нужно >= 60% (т.е. 3 из 5)
QUIZ_SIZE = 5
PASS_RATIO = 0.6

# Распознавание речи
SAMPLE_RATE = 16000
BLOCK_SIZE = 4000            # 0.25 с аудио за блок — частые «живые» результаты
STT_MODEL_FAST = "fast"      # ~33 МБ, быстро
STT_MODEL_BIG = "big"        # ~1.4 ГБ, максимальная точность

# Статусы Wi-Fi
WIFI_BLOCKED = "blocked"
WIFI_UNBLOCKED = "unblocked"

APP_NAME = "Книжный страж"
APP_VERSION = "2.0.0"
