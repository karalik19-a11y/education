# -*- coding: utf-8 -*-
"""Графический интерфейс «Книжный страж» (Tkinter, тёмная премиальная тема).

Распознавание работает в реальном времени: фоновый поток только кладёт
события в очередь, а все обновления экрана делает главный поток в _tick —
поэтому слова закрашиваются сразу, как только их услышал микрофон.
"""
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox

from . import config, stt
from .db import Library
from .matcher import BookMatcher
from .quiz import QuizSession
from .textnorm import words_of, spans_of
from .wifi import WifiController

# ---------------------------------------------------------------- палитра
BG = "#0d1321"        # фон приложения
BG2 = "#111a2e"       # фон шапки
CARD = "#182238"      # карточки
CARD2 = "#1f2b45"     # карточки второго уровня / hover
BORDER = "#2c3a5a"    # рамки
TEXT = "#f4f6fb"      # основной текст
MUTED = "#93a1b8"     # приглушённый текст
FAINT = "#5b6b8c"     # едва видимый текст
ACCENT = "#34d399"    # изумрудный
ACCENT_BG = "#0b3b2c"  # тёмно-изумрудный (кнопки/плашки)
GOLD = "#e9b949"      # золото
GOLD_BG = "#3a2c10"
RED = "#f87171"
RED_BG = "#3d1d24"
PAPER = "#f8f3e7"     # «бумага» для текста книги
PAPER_TEXT = "#211d14"
DONE_BG = "#b9e5c6"   # прочитанные слова
NOW_BG = "#f2cd6e"    # текущее слово

FONT = "Segoe UI"
SERIF = "Georgia"


# ---------------------------------------------------------------- виджеты
class PButton(tk.Button):
    """Кнопка в стиле приложения: плоская, с hover-подсветкой."""

    STYLES = {
        "primary": {"bg": "#059669", "fg": "white", "hover": "#047857",
                    "active": "#065f46", "border": "#059669"},
        "gold": {"bg": GOLD, "fg": "#231a05", "hover": "#f5c95c",
                 "active": "#d9a83a", "border": GOLD},
        "ghost": {"bg": CARD2, "fg": TEXT, "hover": "#27375c",
                  "active": "#16203a", "border": BORDER},
        "danger": {"bg": RED_BG, "fg": "#ffb4b4", "hover": "#52232c",
                   "active": "#33141b", "border": "#7f2d3a"},
        "paper": {"bg": "#efe6d3", "fg": "#4a4232", "hover": "#e2d6bd",
                  "active": "#d3c4a6", "border": "#d3c4a6"},
    }

    def __init__(self, parent, text, style="ghost", command=None, font_size=11,
                 bold=False, padx=16, pady=7, anchor="center", **kw):
        s = self.STYLES[style]
        super().__init__(
            parent, text=text, command=command, relief="flat", bd=0,
            bg=s["bg"], fg=s["fg"], activebackground=s["active"],
            activeforeground=s["fg"], highlightthickness=1,
            highlightbackground=s["border"], highlightcolor=s["border"],
            font=(FONT, font_size, "bold" if bold else "normal"),
            padx=padx, pady=pady, anchor=anchor, cursor="hand2", **kw,
        )
        self._hover = s["hover"]
        self._normal = s["bg"]
        self.bind("<Enter>", lambda e: self.config(bg=self._hover))
        self.bind("<Leave>", lambda e: self.config(bg=self._normal))


class Card(tk.Frame):
    def __init__(self, parent, bg=CARD, **kw):
        super().__init__(parent, bg=bg, highlightthickness=1,
                         highlightbackground=BORDER, **kw)


class Bar(tk.Canvas):
    """Скруглённый прогресс-бар."""

    def __init__(self, parent, width=520, height=14, bg=CARD, fill=ACCENT,
                 track="#0e1628"):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self._w = width
        self._h = height
        self._fill = fill
        self._track = track
        self._value = 0.0
        self._draw()

    def set(self, value):
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._value) < 0.002:
            return
        self._value = value
        self._draw()

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        self.create_oval(x0, y0, x0 + 2 * r, y1, outline="", **kw)
        self.create_oval(x1 - 2 * r, y0, x1, y1, outline="", **kw)
        self.create_rectangle(x0 + r, y0, x1 - r, y1, outline="", **kw)

    def _draw(self):
        self.delete("all")
        w, h, r = self._w, self._h, self._h // 2
        self._round_rect(0, 0, w, h, r, fill=self._track)
        fw = max(0, int(w * self._value))
        if fw > 0:
            if fw < 2 * r:
                self.create_oval(0, 0, fw, h, outline="", fill=self._fill)
            else:
                self._round_rect(0, 0, fw, h, r, fill=self._fill)


class LevelMeter(tk.Canvas):
    """Индикатор громкости микрофона (живая шкала)."""

    def __init__(self, parent, width=110, height=10, bg=BG2, n=12):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self._w = width
        self._h = height
        self._n = n

    def set(self, level):
        self.delete("all")
        lit = int(round(max(0.0, min(1.0, level)) * self._n))
        gap = 3
        sw = (self._w - gap * (self._n - 1)) / self._n
        for i in range(self._n):
            x0 = i * (sw + gap)
            if i < lit:
                c = ACCENT if i < self._n * 0.65 else (GOLD if i < self._n * 0.85 else RED)
            else:
                c = "#24314f"
            self.create_rectangle(x0, 0, x0 + sw, self._h, outline="", fill=c)


# ---------------------------------------------------------------- сессия
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

        bid = lib.get("current_book_id")
        if bid and lib.book(int(bid)):
            self.book = lib.book(int(bid))
            self.text = self.book["text"]
            self.word_spans = spans_of(self.text)
            self.matcher = BookMatcher(words_of(self.text))
            self.matcher.pos = int(lib.get("word_pos", 0) or 0)
            for p in lib.db.execute(
                "SELECT * FROM pages WHERE book_id=? ORDER BY page_no", (self.book["id"],)
            ):
                self.pages[p["page_no"]] = p
        else:
            self.new_cycle(first=True)

    def bind_book(self):
        self.text = self.book["text"]
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

    def required_words(self):
        need_pages = min(self.lib.pages_required(), max(self.pages))
        p = self.pages.get(need_pages)
        return p["word_end"] if p else 0

    def pages_done(self):
        if self.matcher.pos <= 0:
            return 0
        page_no = self.lib.page_of_word(self.book["id"], max(0, self.matcher.pos - 1))
        return page_no or 0

    def current_page(self):
        page_no = self.lib.page_of_word(self.book["id"], self.matcher.pos)
        return page_no or 1

    def reading_done(self):
        return self.matcher.pos >= self.required_words()

    def quiz_allowed(self):
        return self.reading_done()

    def quiz_passed(self):
        return self.lib.get("quiz_passed", "0") == "1"

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


# ---------------------------------------------------------------- приложение
class App(tk.Tk):
    def __init__(self, demo=False, simulate_wifi=False):
        super().__init__()
        self.title("Книжный страж — читай книгу, открывай Wi-Fi")
        self.configure(bg=BG)
        self.geometry("1040x740")
        self.minsize(920, 640)
        self.demo = demo
        self.lib = Library()
        self.wifi = WifiController(simulate=simulate_wifi or demo)
        self.session = Session(self.lib, self.wifi)
        # ВАЖНО: Wi-Fi блокируется ТОЛЬКО ПОСЛЕ того, как окно открылось
        # (см. _startup_lock). Если блокировать его здесь, в конструкторе,
        # то при любой ошибке старта приложение умрёт, а компьютер останется
        # БЕЗ интернета и БЕЗ интерфейса, где ввести экстренный пароль.
        self._lock_scheduled = False

        # события из фонового потока распознавания -> главный поток
        self.events = queue.Queue()
        self.recognizer = None
        self.reading = False
        self.quiz = None
        self.level = 0.0
        self._last_save = 0.0
        self._done_notified = False
        self._err_notified = False

        self._build_chrome()
        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True)
        self.frames = {}
        for F in (HomeFrame, ReadFrame, QuizFrame):
            fr = F(self.container, self)
            self.frames[F.__name__] = fr
            fr.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.show("HomeFrame")
        self.after(0, self._startup_lock)  # окно показано — теперь блокируем Wi-Fi
        self.after(90, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------ Wi-Fi: сначала окно, потом блокировка
    def _startup_lock(self):
        """Заблокировать Wi-Fi ПОСЛЕ открытия окна.

        Блокировка выполняется в фоновом потоке (netsh/nmcli/rfkill могут
        работать секунды), чтобы не подвешивать интерфейс; при неудаче
        пользователю сообщается, что нужны права администратора.
        """
        if self._lock_scheduled:
            return
        self._lock_scheduled = True
        self._apply_wifi_async(False)

    def _apply_wifi_async(self, enable, reason=""):
        """Включить/выключить Wi-Fi в фоновом потоке.

        Обновление интерфейса — только в главном потоке (через after()).
        Окно приложения при этом всегда открыто, поэтому пользователь
        никогда не остаётся «без интернета и без окна».
        """
        def work():
            try:
                ok = self.wifi.unblock() if enable else self.wifi.block()
            except Exception as e:  # noqa: BLE001
                ok, err = False, str(e)
            else:
                err = self.wifi.last_error
            try:
                self.after(0, lambda: self._wifi_done(enable, ok, err, reason))
            except Exception:  # noqa: BLE001 — окно уже закрыто
                pass

        threading.Thread(target=work, daemon=True).start()

    def _wifi_done(self, enable, ok, err, reason=""):
        try:
            self.lib.set("wifi_state", config.WIFI_UNBLOCKED if enable else config.WIFI_BLOCKED)
            if reason and enable:
                self.lib.set("unlocked_reason", reason)
            self._refresh_chrome()
            try:
                self.frames["HomeFrame"].refresh()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            return
        if ok or self.wifi.simulate:
            return
        verb = "включить" if enable else "заблокировать"
        try:
            messagebox.showwarning(
                f"Не удалось {verb} Wi-Fi",
                "Недостаточно прав (нужен администратор / sudo).\n\n"
                "Запустите программу от имени администратора (Windows)\n"
                "или через sudo (Linux/macOS) — иначе Wi-Fi не будет блокироваться.\n\n"
                f"{str(err).strip()[:300]}",
            )
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- каркас
    def _build_chrome(self):
        top = tk.Frame(self, bg=BG2, highlightthickness=0)
        top.pack(fill="x")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        left = tk.Frame(top, bg=BG2)
        left.pack(side="left", padx=16, pady=10)
        tk.Label(left, text="◈", bg=BG2, fg=GOLD, font=(FONT, 16, "bold")).pack(
            side="left", padx=(0, 8))
        tk.Label(left, text="Книжный страж", bg=BG2, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(side="left")
        self.wifi_badge = tk.Label(
            left, text="", bg=RED_BG, fg=RED, font=(FONT, 10, "bold"), padx=10, pady=3,
        )
        self.wifi_badge.pack(side="left", padx=14)

        right = tk.Frame(top, bg=BG2)
        right.pack(side="right", padx=16, pady=10)
        if self.demo:
            tk.Label(right, text="ДЕМО", bg=GOLD_BG, fg=GOLD,
                     font=(FONT, 9, "bold"), padx=8, pady=3).pack(side="left", padx=6)
        PButton(right, text="🔑  Экстренный доступ", style="danger",
                font_size=10, command=self.ask_password).pack(side="left")
        self._refresh_chrome()

    def _refresh_chrome(self):
        st = self.lib.get("wifi_state", config.WIFI_BLOCKED)
        if st == config.WIFI_UNBLOCKED:
            self.wifi_badge.config(text="●  Wi-Fi ВКЛЮЧЁН", bg=ACCENT_BG, fg=ACCENT)
        else:
            self.wifi_badge.config(text="●  Wi-Fi ЗАБЛОКИРОВАН", bg=RED_BG, fg=RED)

    def show(self, name):
        self.frames[name].tkraise()
        self.frames[name].on_show()

    # ------------------------------------------------------- главный цикл GUI
    def _tick(self):
        # забрать события распознавания (всё обновление экрана — здесь)
        for _ in range(60):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "words":
                    self._handle_words(payload)
                elif kind == "partial":
                    self.frames["ReadFrame"].set_partial(payload)
                elif kind == "level":
                    self.level = float(payload or 0.0)
                elif kind == "error":
                    self._handle_stt_error(payload)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.frames["ReadFrame"].pulse()
        except Exception:  # noqa: BLE001
            pass
        self.after(90, self._tick)

    def _on_close(self):
        self.stop_reading()
        try:
            self.session.save()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.lib.close()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    # ---------------------------------------------------------------- пароль
    def ask_password(self):
        dlg = tk.Toplevel(self)
        dlg.title("Экстренный доступ")
        dlg.configure(bg=CARD)
        dlg.geometry("400x230")
        dlg.transient(self)
        dlg.grab_set()
        tk.Label(dlg, text="🔑", bg=CARD, fg=GOLD, font=(FONT, 26)).pack(pady=(18, 2))
        tk.Label(
            dlg, text="Экстренный доступ",
            bg=CARD, fg=TEXT, font=(FONT, 14, "bold"),
        ).pack()
        tk.Label(
            dlg, text="Введите пароль, чтобы немедленно включить Wi-Fi:",
            bg=CARD, fg=MUTED, font=(FONT, 10),
        ).pack(pady=(4, 8))
        var = tk.StringVar()
        ent = tk.Entry(dlg, textvariable=var, show="•", font=(FONT, 15),
                       justify="center", bg=BG, fg=TEXT, insertbackground=TEXT,
                       relief="flat", highlightthickness=1, highlightbackground=BORDER,
                       width=20)
        ent.pack(pady=4, ipady=5)
        ent.focus_set()
        row = tk.Frame(dlg, bg=CARD)
        row.pack(pady=12)

        def try_pass(event=None):
            if var.get().strip() == config.EMERGENCY_PASSWORD:
                self._apply_wifi_async(True, "password")
                self._refresh_chrome()
                dlg.destroy()
                messagebox.showinfo(
                    "Wi-Fi включён",
                    "Верный пароль! Wi-Fi включён.\n"
                    "(Чтобы снова заблокировать — нажмите «Заблокировать Wi-Fi».)",
                )
                self.frames["HomeFrame"].refresh()
            else:
                messagebox.showerror("Ошибка", "Неверный пароль.", parent=dlg)
                var.set("")

        PButton(row, text="Включить Wi-Fi", style="gold", command=try_pass).pack(
            side="left", padx=6)
        PButton(row, text="Отмена", command=dlg.destroy).pack(side="left", padx=6)
        ent.bind("<Return>", try_pass)

    # ---------------------------------------------------------------- чтение
    def mic_device(self):
        v = self.lib.get("mic_device", "")
        if v in (None, "", "None"):
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def start_reading(self):
        if self.reading:
            return
        words = self.session.matcher.words
        device = self.mic_device()
        try:
            if self.demo:
                remaining = words[self.session.matcher.pos:]
                self.recognizer = stt.FakeRecognizer(
                    remaining, words_per_second=90, noise_every=20,
                    on_words=lambda w: self.events.put(("words", w)),
                    on_partial=lambda s: self.events.put(("partial", s)),
                    on_level=lambda v: self.events.put(("level", v)),
                )
            else:
                self.recognizer = stt.VoskRecognizer(
                    on_words=lambda w: self.events.put(("words", w)),
                    on_partial=lambda s: self.events.put(("partial", s)),
                    on_level=lambda v: self.events.put(("level", v)),
                    on_error=lambda e: self.events.put(("error", e)),
                    device=device,
                )
            self.recognizer.start()
        except RuntimeError as e:
            # выбранный микрофон недоступен — пробуем микрофон по умолчанию
            if not self.demo and device is not None:
                try:
                    self.lib.set("mic_device", "")
                    self.recognizer = stt.VoskRecognizer(
                        on_words=lambda w: self.events.put(("words", w)),
                        on_partial=lambda s: self.events.put(("partial", s)),
                        on_level=lambda v: self.events.put(("level", v)),
                        on_error=lambda e: self.events.put(("error", e)),
                        device=None,
                    )
                    self.recognizer.start()
                    self.reading = True
                    self.frames["ReadFrame"].set_status(
                        "Выбранный микрофон недоступен — включён микрофон по умолчанию.")
                    self.frames["ReadFrame"].sync_controls()
                    return
                except RuntimeError:
                    pass
            messagebox.showerror("Не удалось начать чтение", str(e))
            self.recognizer = None
            self.frames["ReadFrame"].sync_controls()
            return
        self.reading = True
        self._err_notified = False
        self.frames["ReadFrame"].set_status("🎙 Слушаю… читайте текст вслух — слова подсвечиваются сразу.")
        self.frames["ReadFrame"].sync_controls()

    def stop_reading(self):
        if self.recognizer:
            try:
                self.recognizer.stop()
            except Exception:  # noqa: BLE001
                pass
            self.recognizer = None
        # события-«хвосты» фразы ещё в очереди — _tick их дозаберёт
        self.reading = False
        try:
            self.session.save()
        except Exception:  # noqa: BLE001
            pass
        self.level = 0.0
        try:
            self.frames["ReadFrame"].sync_controls()
        except Exception:  # noqa: BLE001
            pass

    def _handle_words(self, words):
        """Выполняется в главном потоке: матчинг + подсветка + сохранение."""
        if not words:
            return
        res = self.session.matcher.feed(words)
        import time as _t
        now = _t.monotonic()
        if now - self._last_save > 2.0:  # не дёргаем диск на каждом слове
            self._last_save = now
            try:
                self.session.save()
            except Exception:  # noqa: BLE001
                pass
        fr = self.frames["ReadFrame"]
        fr.on_progress(res)
        if self.session.reading_done():
            if not self._done_notified:
                self._done_notified = True
                messagebox.showinfo(
                    "50 страниц прочитаны!",
                    "Отлично! Вы прочитали 50 страниц вслух.\nТеперь можно пройти лёгкий тест.",
                )
        else:
            self._done_notified = False
        if res.wrong_book:
            fr.warn_wrong_book()

    def _handle_stt_error(self, msg):
        if not msg or self._err_notified:
            return
        if "кадра" in str(msg):
            return  # одиночные сбои кадров — только в статус, без окна
        self._err_notified = True
        self.frames["ReadFrame"].set_status(f"⚠ {msg}")
        messagebox.showwarning("Распознавание речи", str(msg))

    def new_cycle(self):
        self.stop_reading()
        self.session.new_cycle()
        self._done_notified = False
        self._refresh_chrome()
        self.frames["HomeFrame"].refresh()

    # ---------------------------------------------------------------- тест
    def start_quiz(self):
        if not self.session.quiz_allowed():
            messagebox.showwarning(
                "Рано", "Сначала прочитайте 50 страниц вслух (прогресс виден на главном экране)."
            )
            return
        self.stop_reading()
        qs = self.lib.questions(self.session.book["id"])
        self.quiz = QuizSession(qs)
        self.show("QuizFrame")

    def finish_quiz(self):
        q = self.quiz
        if q.passed():
            self.session.lib.set("quiz_passed", 1)
            self._apply_wifi_async(True, "quiz")
            self._refresh_chrome()
            messagebox.showinfo(
                "Доступ открыт!",
                f"{q.result_text()}\n\nWi-Fi включён! Хорошего отдыха.\n"
                "Когда захотите новую книгу — нажмите «Новый цикл».",
            )
        else:
            messagebox.showwarning(
                "Тест не пройден",
                f"{q.result_text()}\n\nПеречитайте страницы и попробуйте ещё раз.",
            )
        self.show("HomeFrame")


# ---------------------------------------------------------------- главная
class HomeFrame(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=48, pady=20)

        tk.Label(wrap, text="ШКОЛЬНАЯ ПРОГРАММА · 7 КЛАСС", bg=BG, fg=GOLD,
                 font=(FONT, 10, "bold")).pack(pady=(6, 2))
        tk.Label(wrap, text="Ваша книга на сегодня", bg=BG, fg=MUTED,
                 font=(FONT, 12)).pack()

        hero = Card(wrap)
        hero.pack(fill="x", pady=12)
        inner = tk.Frame(hero, bg=CARD)
        inner.pack(fill="x", padx=28, pady=20)
        self.lbl_author = tk.Label(inner, text="", bg=CARD, fg=GOLD,
                                   font=(FONT, 12, "bold"))
        self.lbl_author.pack()
        self.lbl_title = tk.Label(inner, text="", bg=CARD, fg=TEXT,
                                  font=(SERIF, 24, "bold"), wraplength=760,
                                  justify="center")
        self.lbl_title.pack(pady=(2, 2))
        self.lbl_tag = tk.Label(inner, text="", bg=CARD, fg=MUTED, font=(FONT, 11))
        self.lbl_tag.pack()

        prog = Card(wrap)
        prog.pack(fill="x", pady=(0, 12))
        prow = tk.Frame(prog, bg=CARD)
        prow.pack(fill="x", padx=28, pady=16)
        self.lbl_prog = tk.Label(prow, text="", bg=CARD, fg=TEXT, font=(FONT, 14, "bold"))
        self.lbl_prog.pack(anchor="w")
        self.bar = Bar(prow, width=760, height=14)
        self.bar.pack(fill="x", pady=(10, 6))
        stats = tk.Frame(prow, bg=CARD)
        stats.pack(fill="x")
        self.lbl_words = tk.Label(stats, text="", bg=CARD, fg=MUTED, font=(FONT, 10))
        self.lbl_words.pack(side="left")
        self.lbl_acc = tk.Label(stats, text="", bg=CARD, fg=MUTED, font=(FONT, 10))
        self.lbl_acc.pack(side="right")
        self.lbl_note = tk.Label(prow, text="", bg=CARD, fg=GOLD, font=(FONT, 10, "bold"))
        self.lbl_note.pack(anchor="w", pady=(6, 0))

        btns = tk.Frame(wrap, bg=BG)
        btns.pack(pady=6)
        PButton(btns, text="▶   Начать чтение вслух", style="primary",
                font_size=13, bold=True, padx=26, pady=10,
                command=self._read).grid(row=0, column=0, padx=8, pady=6, sticky="ew")
        PButton(btns, text="📝   Пройти тест", style="gold",
                font_size=13, bold=True, padx=26, pady=10,
                command=self.app.start_quiz).grid(row=0, column=1, padx=8, pady=6, sticky="ew")
        PButton(btns, text="🔒  Заблокировать Wi-Fi",
                command=self._relock).grid(row=1, column=0, padx=8, pady=6, sticky="ew")
        PButton(btns, text="🔄  Новый цикл (новая книга)",
                command=self._new_cycle).grid(row=1, column=1, padx=8, pady=6, sticky="ew")
        btns.grid_columnconfigure(0, weight=1)
        btns.grid_columnconfigure(1, weight=1)

        self.lbl_wifi = tk.Label(wrap, text="", bg=BG, font=(FONT, 11, "bold"))
        self.lbl_wifi.pack(pady=(6, 0))
        tk.Label(wrap, text="Читайте вслух текст на экране — слова подсвечиваются в реальном времени,\n"
                            "как только микрофон их услышал. Паузы не нужны.",
                 bg=BG, fg=FAINT, font=(FONT, 10), justify="center").pack(pady=(6, 0))

    def _read(self):
        self.app.show("ReadFrame")  # чтение стартует само при открытии экрана

    def _relock(self):
        self.app._apply_wifi_async(False)
        self.app._refresh_chrome()
        self.refresh()

    def _new_cycle(self):
        if self.app.session.reading_done() and not self.app.session.quiz_passed():
            if not messagebox.askyesno("Новый цикл", "50 страниц уже прочитаны. "
                                                     "Прогресс сбросится. Продолжить?"):
                return
        self.app.new_cycle()

    def on_show(self):
        self.refresh()

    def refresh(self):
        s = self.app.session
        b = s.book
        self.lbl_author.config(text=b["author"])
        self.lbl_title.config(text=f"«{b['title']}»")
        self.lbl_tag.config(text=b["tagline"] or "")
        done = s.pages_done()
        need = self.app.lib.pages_required()
        self.lbl_prog.config(text=f"Прочитано вслух: {min(done, need)} из {need} страниц")
        self.bar.set(done / max(1, need))
        need_words = s.required_words()
        self.lbl_words.config(
            text=f"Слов засчитано: {min(s.matcher.pos, need_words)} из {need_words}")
        rate = s.matcher.match_rate()
        if s.matcher.fed > 10:
            self.lbl_acc.config(text=f"Точность распознавания: {rate:.0%}")
        else:
            self.lbl_acc.config(text="Точность распознавания: —")
        if s.quiz_passed():
            self.lbl_note.config(text="✓ Тест пройден — Wi-Fi открыт. Можно взять новую книгу.",
                                 fg=ACCENT)
        elif s.reading_done():
            self.lbl_note.config(text="✓ Чтение завершено! Остался лёгкий тест →", fg=ACCENT)
        else:
            self.lbl_note.config(
                text=f"До теста осталось: {max(0, need - done)} стр.", fg=GOLD)
        st = self.app.lib.get("wifi_state", config.WIFI_BLOCKED)
        self.lbl_wifi.config(
            text="● Wi-Fi сейчас ВКЛЮЧЁН" if st == config.WIFI_UNBLOCKED else "● Wi-Fi сейчас ЗАБЛОКИРОВАН",
            fg=ACCENT if st == config.WIFI_UNBLOCKED else RED,
        )


# ---------------------------------------------------------------- чтение
class ReadFrame(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.page_no = 1
        self._painted_upto = 0
        self._blink = False
        self._disp_level = 0.0
        self._mics = []

        # --- верхняя строка: назад · страница · live-индикатор
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=24, pady=(14, 2))
        PButton(head, text="‹ Главная", font_size=10,
                command=self._home).pack(side="left")
        self.lbl_page = tk.Label(head, text="", bg=BG, fg=TEXT, font=(FONT, 12, "bold"))
        self.lbl_page.pack(side="left", padx=16)

        live = tk.Frame(head, bg=BG)
        live.pack(side="right")
        self.rec_canvas = tk.Canvas(live, width=14, height=14, bg=BG,
                                    highlightthickness=0, bd=0)
        self.rec_canvas.pack(side="left", padx=(0, 6))
        self.lbl_live = tk.Label(live, text="LIVE", bg=BG, fg=FAINT, font=(FONT, 10, "bold"))
        self.lbl_live.pack(side="left", padx=(0, 10))
        self.meter = LevelMeter(live, bg=BG)
        self.meter.pack(side="left")

        self.lbl_status = tk.Label(self, text="", bg=BG, fg=MUTED, font=(FONT, 10),
                                   wraplength=940, justify="left")
        self.lbl_status.pack(fill="x", padx=24)
        self.lbl_warn = tk.Label(self, text="", bg=BG, fg=RED, font=(FONT, 11, "bold"),
                                 wraplength=940)
        self.lbl_warn.pack(fill="x", padx=24)

        # --- «бумага» с текстом книги
        paper_wrap = tk.Frame(self, bg="#0a0f1c", highlightthickness=1,
                              highlightbackground=BORDER)
        paper_wrap.pack(fill="both", expand=True, padx=24, pady=8)
        self.text = tk.Text(paper_wrap, wrap="word", font=(SERIF, 14), bg=PAPER,
                            fg=PAPER_TEXT, padx=26, pady=18, spacing1=3, spacing3=6,
                            relief="flat", bd=0, highlightthickness=0,
                            selectbackground="#d8cfae", insertbackground=PAPER_TEXT)
        scroll = tk.Scrollbar(paper_wrap, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("done", background=DONE_BG)
        self.text.tag_configure("now", background=NOW_BG)
        self.text.bind("<Left>", lambda e: (self.turn(-1), "break")[1])
        self.text.bind("<Right>", lambda e: (self.turn(1), "break")[1])
        self.text.bind("<space>", lambda e: (self.toggle_pause(), "break")[1])
        self.text.bind("<Key>", lambda e: "break")  # текст книги менять нельзя

        # --- живая строка «услышано»
        heard = Card(self)
        heard.pack(fill="x", padx=24, pady=(0, 4))
        hrow = tk.Frame(heard, bg=CARD)
        hrow.pack(fill="x", padx=14, pady=7)
        tk.Label(hrow, text="🎤", bg=CARD, font=(FONT, 11)).pack(side="left")
        self.lbl_partial = tk.Label(hrow, text="микрофон ждёт ваш голос…", bg=CARD,
                                    fg=FAINT, font=(FONT, 11, "italic"),
                                    wraplength=860, justify="left")
        self.lbl_partial.pack(side="left", padx=(6, 0), fill="x", expand=True)

        # --- микрофон
        microw = tk.Frame(self, bg=BG)
        microw.pack(fill="x", padx=24, pady=(2, 0))
        tk.Label(microw, text="Микрофон:", bg=BG, fg=FAINT, font=(FONT, 10)).pack(
            side="left")
        self.btn_mic = PButton(microw, text="…", font_size=10, command=self._cycle_mic)
        self.btn_mic.pack(side="left", padx=8)
        tk.Label(microw, text="(нажмите, чтобы выбрать другой)", bg=BG, fg=FAINT,
                 font=(FONT, 9)).pack(side="left")

        # --- управление
        row = tk.Frame(self, bg=BG)
        row.pack(pady=10)
        PButton(row, text="◀", padx=14, command=lambda: self.turn(-1)).pack(
            side="left", padx=4)
        self.btn_pause = PButton(row, text="⏸ Пауза", style="primary", font_size=12,
                                 bold=True, padx=22, command=self.toggle_pause)
        self.btn_pause.pack(side="left", padx=4)
        PButton(row, text="▶", padx=14, command=lambda: self.turn(1)).pack(
            side="left", padx=4)
        PButton(row, text="📝 К тесту", style="gold",
                command=self._quiz).pack(side="left", padx=(18, 4))
        PButton(row, text="На главную",
                command=self._home).pack(side="left", padx=4)

    # --------- отображение
    def on_show(self):
        s = self.app.session
        self.page_no = s.current_page()
        self._refresh_mics()
        self.load_page()
        if not self.app.reading and not s.reading_done():
            self.set_status("🎙 Включаю микрофон — читайте вслух, слова подсвечиваются сразу…")
        if not self.app.reading and not s.reading_done():
            self.app.start_reading()
        self.sync_controls()

    def load_page(self):
        s = self.app.session
        page = s.pages.get(self.page_no)
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.tag_remove("done", "1.0", "end")
        self.text.tag_remove("now", "1.0", "end")
        if page:
            self.text.insert("1.0", s.text[page["char_start"]:page["char_end"]])
        self.text.config(state="disabled")
        self._painted_upto = page["word_start"] if page else 0
        if page:
            self._paint_range(page, self._painted_upto, s.matcher.pos)
            self._paint_now(page)
            self._scroll_to_now(page)
        need = self.app.lib.pages_required()
        total = max(s.pages)
        self.lbl_page.config(
            text=f"Страница {self.page_no} из {total}"
                 + (f"  ·  цель: {need} стр." if self.page_no <= need else "  ·  сверх программы")
        )

    def _paint_range(self, page, wfrom, wto):
        """Закрасить слова [wfrom, wto) зелёным (в пределах страницы)."""
        if not page or wto <= wfrom:
            return
        s = self.app.session
        lo = max(wfrom, page["word_start"])
        hi = min(wto, page["word_end"], len(s.word_spans))
        base = page["char_start"]
        for i in range(lo, hi):
            a, b = s.word_spans[i]
            self.text.tag_add("done", f"1.0+{a - base}c", f"1.0+{b - base}c")
        self._painted_upto = max(self._painted_upto, hi)

    def _paint_now(self, page):
        self.text.tag_remove("now", "1.0", "end")
        if not page:
            return
        s = self.app.session
        cur = s.matcher.pos
        if page["word_start"] <= cur < page["word_end"] and cur < len(s.word_spans):
            a, b = s.word_spans[cur]
            base = page["char_start"]
            self.text.tag_add("now", f"1.0+{a - base}c", f"1.0+{b - base}c")

    def _scroll_to_now(self, page):
        s = self.app.session
        cur = s.matcher.pos
        if page and page["word_start"] <= cur < page["word_end"] and cur < len(s.word_spans):
            b = s.word_spans[cur][1] - page["char_start"]
            try:
                self.text.see(f"1.0+{b}c")
            except Exception:  # noqa: BLE001
                pass

    def turn(self, delta):
        s = self.app.session
        total = max(s.pages)
        self.page_no = max(1, min(total, self.page_no + delta))
        self.load_page()

    def toggle_pause(self):
        if self.app.reading:
            self.app.stop_reading()
            self.set_status("⏸ Пауза — распознавание остановлено. Нажмите «Продолжить», чтобы читать дальше.")
        else:
            self.app.start_reading()
        self.sync_controls()

    def sync_controls(self):
        if self.app.reading:
            self.btn_pause.config(text="⏸ Пауза")
        else:
            self.btn_pause.config(text="▶ Продолжить")

    def _quiz(self):
        self.app.start_quiz()

    def _home(self):
        self.app.stop_reading()
        self.app.show("HomeFrame")

    # --------- микрофон
    def _refresh_mics(self):
        try:
            self._mics = stt.input_devices()
        except Exception:  # noqa: BLE001
            self._mics = []
        self._sync_mic_label()

    def _sync_mic_label(self):
        cur = self.app.mic_device()
        name = None
        if self._mics:
            for idx, nm in self._mics:
                if idx == cur:
                    name = nm
                    break
            if name is None:
                name = self._mics[0][1] if cur is None else f"#{cur}"
        else:
            name = "микрофон по умолчанию"
        short = name if len(name) <= 44 else name[:42] + "…"
        try:
            self.btn_mic.config(text=f"🎙 {short}")
        except Exception:  # noqa: BLE001
            pass

    def _cycle_mic(self):
        self._refresh_mics()
        valid = [(i, n) for i, n in self._mics if i >= 0]
        if not valid:
            self.set_status("⚠ Микрофоны не найдены. Проверьте подключение и доступ к микрофону.")
            return
        cur = self.app.mic_device()
        order = [None] + [i for i, _ in valid]
        try:
            nxt = order[(order.index(cur) + 1) % len(order)]
        except ValueError:
            nxt = None
        self.app.lib.set("mic_device", "" if nxt is None else str(nxt))
        self._sync_mic_label()
        if self.app.reading:
            self.app.stop_reading()
            self.app.start_reading()
        else:
            self.set_status("Микрофон выбран. Нажмите «Продолжить» и читайте вслух.")

    # --------- события от распознавания (уже в главном потоке)
    def set_status(self, t):
        try:
            self.lbl_status.config(text=t)
        except Exception:  # noqa: BLE001
            pass

    def set_partial(self, s):
        try:
            if s:
                txt = s if len(s) <= 110 else "…" + s[-109:]
                self.lbl_partial.config(text=f"«{txt}»", fg=MUTED)
            else:
                self.lbl_partial.config(text="микрофон ждёт ваш голос…", fg=FAINT)
        except Exception:  # noqa: BLE001
            pass

    def on_progress(self, res):
        s = self.app.session
        page = s.pages.get(self.page_no)
        if page:
            # инкрементальная подсветка: красим только новые слова
            self._paint_range(page, self._painted_upto, s.matcher.pos)
            self._paint_now(page)
            self._scroll_to_now(page)
        # авто-перелистывание за прогрессом
        cur = s.current_page()
        if cur != self.page_no:
            self.page_no = cur
            self.load_page()
        done = s.pages_done()
        need = self.app.lib.pages_required()
        self.set_status(
            f"Совпало: {res.matched} · пропущено слов книги: {res.skipped} · "
            f"мимо: {res.not_matched}   ·   прочитано {min(done, need)}/{need} стр.")
        if not res.wrong_book:
            self.lbl_warn.config(text="")

    def warn_wrong_book(self):
        self.lbl_warn.config(
            text="⚠ Похоже, вы читаете не ту книгу (или совсем другой текст)! "
                 "Прогресс не засчитывается."
        )

    def pulse(self):
        # мигание LIVE-точки и живая шкала громкости
        r = self.rec_canvas
        r.delete("all")
        if self.app.reading:
            self._blink = not self._blink
            c = "#f87171" if self._blink else "#7f1d2e"
            self.lbl_live.config(fg=RED)
        else:
            c = "#24314f"
            self.lbl_live.config(fg=FAINT)
        r.create_oval(2, 2, 12, 12, outline="", fill=c)
        target = self.app.level if self.app.reading else 0.0
        # плавный спад шкалы
        self._disp_level = max(target, self._disp_level * 0.82)
        try:
            self.meter.set(self._disp_level)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- тест
class QuizFrame(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=90, pady=24)
        tk.Label(wrap, text="ПРОВЕРКА ЗНАНИЙ", bg=BG, fg=GOLD,
                 font=(FONT, 10, "bold")).pack(pady=(8, 2))
        self.lbl_head = tk.Label(wrap, text="", bg=BG, fg=TEXT, font=(FONT, 18, "bold"))
        self.lbl_head.pack()
        self.bar = Bar(wrap, width=560, height=10, bg=BG, fill=GOLD)
        self.bar.pack(pady=10)

        card = Card(wrap)
        card.pack(fill="x", pady=8)
        self.lbl_q = tk.Label(card, text="", bg=CARD, fg=TEXT, font=(SERIF, 15),
                              wraplength=700, justify="left")
        self.lbl_q.pack(padx=28, pady=(22, 6), anchor="w")
        self.lbl_about = tk.Label(card, text="", bg=CARD, fg=FAINT, font=(FONT, 9))
        self.lbl_about.pack(anchor="w", padx=28, pady=(0, 16))

        self.opts_box = tk.Frame(wrap, bg=BG)
        self.opts_box.pack(fill="x", pady=6)
        self.opt_btns = []
        for i in range(4):
            b = PButton(self.opts_box, text="", font_size=12, anchor="w", padx=18, pady=9,
                        command=lambda i=i: self._answer(i))
            b.pack(fill="x", pady=4)
            self.opt_btns.append(b)
        tk.Label(wrap, text="Нажмите на вариант ответа — он засчитается сразу.",
                 bg=BG, fg=FAINT, font=(FONT, 10)).pack(pady=(8, 0))
        brow = tk.Frame(wrap, bg=BG)
        brow.pack(pady=8)
        PButton(brow, text="‹ На главную", command=lambda: self.app.show("HomeFrame")).pack()

    def on_show(self):
        self.load()

    def load(self):
        q = self.app.quiz.current() if self.app.quiz else None
        if q is None:
            return
        n = self.app.quiz.index + 1
        total = len(self.app.quiz.questions)
        self.lbl_head.config(text=f"Вопрос {n} из {total}")
        self.bar.set((n - 1) / max(1, total))
        self.lbl_q.config(text=q["question"])
        for i, opt in enumerate(q["options"]):
            self.opt_btns[i].config(text=f"{'ABCD'[i]}.  {opt}")
        self.lbl_about.config(text=q["about"] or "")

    def _answer(self, choice):
        q = self.app.quiz
        if q is None or q.current() is None:
            return
        q.answer(choice)
        if q.finished():
            self.bar.set(1.0)
            self.app.finish_quiz()
        else:
            self.load()
