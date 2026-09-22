# -*- coding: utf-8 -*-
"""Состояние цикла чтения: назначенная книга, прогресс, Wi-Fi.

Выделено из app.py в отдельный модуль без зависимости от Tkinter,
чтобы логику можно было проверять самопроверкой (selftest) даже там,
где нет графического режима.
"""
from . import config
from .db import Library
from .matcher import BookMatcher
from .textnorm import words_of, spans_of
from .wifi import WifiController


class Session:
    """Состояние цикла: книга, прогресс чтения, Wi-Fi."""

    def __init__(self, lib: Library, wifi: WifiController):
        self.lib = lib
        self.wifi = wifi
        self.book = None
        self.matcher = None
        self.text = ""
        self.word_spans = []
        self.pages = {}

        try:
            bid = int(lib.get("current_book_id") or 0)
        except (TypeError, ValueError):
            bid = 0  # в state лежал мусор — начинаем новый цикл
        restored = None
        if bid:
            try:
                restored = lib.book(bid)
            except Exception:  # noqa: BLE001
                restored = None
        if restored is not None:
            self.book = restored
            self.text = self.book["text"] or ""
            self.word_spans = spans_of(self.text)
            self.matcher = BookMatcher(words_of(self.text))
            try:
                pos = int(lib.get("word_pos", 0) or 0)
            except (TypeError, ValueError):
                pos = 0
            self.matcher.pos = self._clamp_pos(pos)
            try:
                for p in lib.db.execute(
                    "SELECT * FROM pages WHERE book_id=? ORDER BY page_no",
                    (self.book["id"],)
                ):
                    self.pages[p["page_no"]] = p
            except Exception:  # noqa: BLE001
                self.pages = {}
        if self.book is None or not self.text or not self.pages:
            # нет книги, пустой текст или нет страниц — начинаем новый цикл,
            # иначе дальше будет деление на пустое и «вечный ноль»
            self.new_cycle(first=True)

    def _clamp_pos(self, pos) -> int:
        total = len(self.matcher.words) if self.matcher else 0
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            return 0
        return max(0, min(pos, total))

    def bind_book(self):
        self.text = self.book["text"] or ""
        self.word_spans = spans_of(self.text)
        self.matcher = BookMatcher(words_of(self.text))
        self.pages = {}
        for p in self.lib.db.execute(
            "SELECT * FROM pages WHERE book_id=? ORDER BY page_no", (self.book["id"],)
        ):
            self.pages[p["page_no"]] = p

    def new_cycle(self, first=False):
        prev = int(self.book["id"]) if self.book else None
        bid = self.lib.random_book_id(exclude=prev)
        self.book = self.lib.book(bid)
        self.bind_book()
        self.matcher.pos = 0
        self.lib.set("current_book_id", bid)
        self.lib.set("word_pos", 0)
        self.lib.set("quiz_passed", 0)

    def save(self):
        self.lib.set("word_pos", self.matcher.pos)

    def last_page_no(self) -> int:
        try:
            return max(self.pages) if self.pages else 1
        except (TypeError, ValueError):
            return 1

    def required_words(self):
        """Сколько слов нужно начитать (конец N-й страницы).

        Если таблица страниц повреждена — требуем всю книгу целиком.
        Возвращать 0 здесь нельзя: reading_done() тогда был бы True сразу.
        """
        try:
            need_pages = min(self.lib.pages_required(), self.last_page_no())
        except Exception:  # noqa: BLE001
            need_pages = self.last_page_no()
        p = self.pages.get(need_pages)
        if p is not None:
            try:
                return max(1, int(p["word_end"]))
            except (TypeError, ValueError):
                pass
        return max(1, len(self.matcher.words) if self.matcher else 1)

    def pages_done(self):
        if not self.pages or self.matcher is None or self.matcher.pos <= 0:
            return 0
        try:
            page_no = self.lib.page_of_word(self.book["id"],
                                            max(0, self.matcher.pos - 1))
        except Exception:  # noqa: BLE001
            return 0
        return page_no or 0

    def current_page(self):
        """Страница, на которой сейчас находится указатель чтения."""
        if not self.pages or self.matcher is None:
            return 1
        try:
            page_no = self.lib.page_of_word(self.book["id"], self.matcher.pos)
        except Exception:  # noqa: BLE001
            page_no = None
        if page_no:
            return page_no
        # указатель в самом конце книги (дальше последней страницы) —
        # показываем последнюю страницу, а не прыгаем на первую
        if self.matcher.pos >= len(self.matcher.words):
            return self.last_page_no()
        return 1

    def reading_done(self):
        req = self.required_words()
        return req > 0 and self.matcher is not None and self.matcher.pos >= req

    def quiz_allowed(self):
        return self.reading_done()

    def quiz_passed(self):
        try:
            return self.lib.get("quiz_passed", "0") == "1"
        except Exception:  # noqa: BLE001
            return False

    # СИНХРОННЫЕ (блокирующие UI-поток) переключения — только для CLI/тестов.
    # GUI использует App._apply_wifi_async(): окно должно быть открыто ДО
    # смены Wi-Fi, а netsh/nmcli работают в фоновом потоке.
    def unlock(self, reason=""):
        self.wifi.unblock()
        self.lib.set("wifi_state", config.WIFI_UNBLOCKED)
        if reason:
            self.lib.set("unlocked_reason", reason)

    def relock(self):
        self.wifi.block()
        self.lib.set("wifi_state", config.WIFI_BLOCKED)
