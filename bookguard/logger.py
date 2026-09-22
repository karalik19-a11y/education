# -*- coding: utf-8 -*-
"""Лёгкое логирование «Книжного стража»: файл + консоль, только стандартная библиотека.

Зачем это нужно: при автозапуске вместе с компьютером консольного окна нет,
и любая ошибка была «тихой» — программа просто не открывалась, а причину
увидеть было негде. Теперь всё пишется в ``data/logs/bookguard.log``
(ротация: до 4 файлов по 512 КБ), а неперехваченные исключения из любых
потоков тоже попадают в журнал.
"""
import logging
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "bookguard"
MAX_BYTES = 512 * 1024
BACKUP_COUNT = 3

_configured = False


def log_path() -> Path:
    """Путь к файлу журнала. Никогда не бросает исключений."""
    try:
        from . import config
        log_dir = config.ROOT / "data" / "logs"
    except Exception:  # noqa: BLE001
        log_dir = Path.cwd() / "data" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "bookguard.log"
    except Exception:  # noqa: BLE001 — каталог только для чтения и т.п.
        import tempfile
        return Path(tempfile.gettempdir()) / "bookguard.log"


def setup(level=logging.INFO) -> logging.Logger:
    """Настроить корневой логгер приложения (идемпотентно)."""
    global _configured
    logger = logging.getLogger(LOG_NAME)
    logger.setLevel(level)
    if _configured:
        return logger
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    # файл — всегда (если получилось открыть)
    try:
        fh = RotatingFileHandler(str(log_path()), maxBytes=MAX_BYTES,
                                 backupCount=BACKUP_COUNT, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:  # noqa: BLE001
        pass
    # консоль — только если она есть (запуск через pythonw даёт stdout=None)
    try:
        stream = sys.stderr if sys.stderr is not None else sys.stdout
        if stream is not None:
            ch = logging.StreamHandler(stream)
            ch.setFormatter(fmt)
            logger.addHandler(ch)
    except Exception:  # noqa: BLE001
        pass
    # неперехваченные исключения — тоже в журнал, а не в пустоту
    try:
        _install_hooks(logger)
    except Exception:  # noqa: BLE001
        pass
    _configured = True
    return logger


def get() -> logging.Logger:
    """Вернуть настроенный логгер приложения."""
    logger = logging.getLogger(LOG_NAME)
    if not _configured:
        return setup()
    return logger


def _install_hooks(logger: logging.Logger):
    old_excepthook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        try:
            logger.error("Неперехваченное исключение:\n%s",
                         "".join(traceback.format_exception(exc_type, exc, tb)))
        except Exception:  # noqa: BLE001
            pass
        try:
            old_excepthook(exc_type, exc, tb)
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = _excepthook

    old_thread_hook = getattr(threading, "excepthook", None)

    def _thread_hook(args):
        try:
            logger.error("Неперехваченное исключение в потоке %s:\n%s",
                         getattr(args, "thread", "?"),
                         "".join(traceback.format_exception(
                             args.exc_type, args.exc_value, args.exc_traceback)))
        except Exception:  # noqa: BLE001
            pass
        try:
            if old_thread_hook is not None:
                old_thread_hook(args)
        except Exception:  # noqa: BLE001
            pass

    threading.excepthook = _thread_hook
