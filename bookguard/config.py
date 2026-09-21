# -*- coding: utf-8 -*-
"""Настройки «Книжного стража»."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "library.db"
MODELS_DIR = ROOT / "models"
VOSK_MODEL_DIR = MODELS_DIR / "vosk-model-small-ru"

# Сколько страниц нужно прочитать (можно менять в БД, таблица state)
DEFAULT_PAGES_REQUIRED = 50
# 1 «страница» = 1000 знаков чистого текста (см. tools/build_library.py)
PAGE_CHARS = 1000

# Экстренный пароль для включения Wi-Fi
EMERGENCY_PASSWORD = "67676767"

# Тест: 5 вопросов, для зачёта нужно >= 60% (т.е. 3 из 5)
QUIZ_SIZE = 5
PASS_RATIO = 0.6

# Распознавание речи
SAMPLE_RATE = 16000

# Статусы Wi-Fi
WIFI_BLOCKED = "blocked"
WIFI_UNBLOCKED = "unblocked"
