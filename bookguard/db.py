# -*- coding: utf-8 -*-
"""Доступ к базе данных библиотеки и состоянию программы."""
import random
import sqlite3
import threading
from pathlib import Path

from . import config


class Library:
    def __init__(self, db_path: Path = None):
        self.path = Path(db_path or config.DB_PATH)
        if not self.path.exists():
            raise FileNotFoundError(
                f"Не найдена база данных: {self.path}\n"
                "Запустите install.sh (или install.bat) — база соберётся автоматически."
            )
        # check_same_thread=False + замок: прогресс чтения сохраняется
        # из фонового потока распознавания, интерфейс читает из главного
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    # ---------------- книги
    def books(self):
        with self._lock:
            return self.db.execute(
                "SELECT id, slug, author, title, tagline, n_pages, n_words FROM books ORDER BY id"
            ).fetchall()

    def book(self, book_id):
        with self._lock:
            return self.db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()

    def random_book_id(self, exclude=None):
        ids = [b["id"] for b in self.books()]
        if exclude in ids and len(ids) > 1:
            ids.remove(exclude)
        return random.choice(ids)

    def page(self, book_id, page_no):
        with self._lock:
            return self.db.execute(
                "SELECT * FROM pages WHERE book_id=? AND page_no=?", (book_id, page_no)
            ).fetchone()

    def page_of_word(self, book_id, word_index):
        with self._lock:
            row = self.db.execute(
                "SELECT page_no FROM pages WHERE book_id=? AND word_start<=? AND word_end>? "
                "ORDER BY page_no LIMIT 1",
                (book_id, word_index, word_index),
            ).fetchone()
            return row["page_no"] if row else None

    def questions(self, book_id):
        with self._lock:
            return self.db.execute(
                "SELECT * FROM questions WHERE book_id=?", (book_id,)
            ).fetchall()

    # ---------------- состояние
    def get(self, key, default=None):
        with self._lock:
            row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def get_int(self, key, default: int) -> int:
        """Целое из state с защитой от мусора (ValueError -> default)."""
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def set(self, key, value):
        with self._lock:
            self.db.execute(
                "INSERT INTO state(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
            self.db.commit()

    def pages_required(self):
        need = self.get_int("pages_required", config.DEFAULT_PAGES_REQUIRED)
        return max(1, min(need, 500))

    def close(self):
        with self._lock:
            try:
                self.db.close()
            except Exception:  # noqa: BLE001 — повторное закрытие безопасно
                pass
