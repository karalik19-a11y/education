# -*- coding: utf-8 -*-
"""Графический интерфейс «Книжный страж» (Tkinter)."""
import random
import tkinter as tk
from tkinter import messagebox, ttk

from . import config, stt, autostart
from .db import Library
from .matcher import BookMatcher
from .quiz import QuizSession
from .textnorm import words_of, spans_of
from .wifi import WifiController

BG = "#f4f1e8"
PANEL = "#fffdf6"
GREEN = "#2e7d32"
RED = "#b71c1c"
DARK = "#333333"


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
            self.matcher = BookMatcher(words_of(self.book["text"]))
            self.matcher.pos = int(lib.get("word_pos", 0) or 0)
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

    def unlock(self, reason=""):
        self.wifi.unblock()
        self.lib.set("wifi_state", config.WIFI_UNBLOCKED)
        if reason:
            self.lib.set("unlocked_reason", reason)

    def relock(self):
        self.wifi.block()
        self.lib.set("wifi_state", config.WIFI_BLOCKED)


class App(tk.Tk):
    def __init__(self, demo=False, simulate_wifi=False):
        super().__init__()
        self.title("Книжный страж — читай книгу, открывай Wi-Fi")
        self.configure(bg=BG)
        self.geometry("980x700")
        self.minsize(860, 620)
        self.demo = demo
        self.lib = Library()
        self.wifi = WifiController(simulate=simulate_wifi or demo)
        self.session = Session(self.lib, self.wifi)
        self.session.relock()

        self.recognizer = None
        self.reading = False
        self.quiz = None

        self._build_style()
        self._build_chrome()
        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True)
        self.frames = {}
        for F in (HomeFrame, ReadFrame, QuizFrame):
            fr = F(self.container, self)
            self.frames[F.__name__] = fr
            fr.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.show("HomeFrame")
        self.after(300, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- каркас
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass
        style.configure("TButton", font=("Segoe UI", 11), padding=6)
        style.configure("Big.TButton", font=("Segoe UI", 12, "bold"), padding=10)
        style.configure("Horizontal.TProgressbar", thickness=18)

    def _build_chrome(self):
        top = tk.Frame(self, bg=PANEL, highlightbackground="#d9d2bd", highlightthickness=1)
        top.pack(fill="x")
        self.wifi_badge = tk.Label(
            top, text="  Wi-Fi ЗАБЛОКИРОВАН  ", bg=RED, fg="white",
            font=("Segoe UI", 11, "bold"), padx=8, pady=4,
        )
        self.wifi_badge.pack(side="left", padx=10, pady=8)
        self.book_label = tk.Label(top, text="", bg=PANEL, fg=DARK, font=("Segoe UI", 11))
        self.book_label.pack(side="left", padx=8)
        ttk.Button(top, text="Экстренный доступ", command=self.ask_password).pack(
            side="right", padx=10, pady=8
        )
        if self.demo:
            tk.Label(top, text="ДЕМО-РЕЖИМ", bg="#ff9800", fg="white",
                     font=("Segoe UI", 9, "bold")).pack(side="right", padx=6)
        self._refresh_chrome()

    def _refresh_chrome(self):
        st = self.lib.get("wifi_state", config.WIFI_BLOCKED)
        if st == config.WIFI_UNBLOCKED:
            self.wifi_badge.config(text="  Wi-Fi ВКЛЮЧЁН  ", bg=GREEN)
        else:
            self.wifi_badge.config(text="  Wi-Fi ЗАБЛОКИРОВАН  ", bg=RED)
        b = self.session.book
        self.book_label.config(text=f"Книга: {b['author']}. {b['title']}")

    def show(self, name):
        self.frames[name].tkraise()
        self.frames[name].on_show()

    # ---------------------------------------------------------------- цикл GUI
    def _tick(self):
        self.frames["ReadFrame"].pulse()
        self.after(400, self._tick)

    def _on_close(self):
        self.stop_reading()
        self.session.save()
        self.lib.close()
        self.destroy()

    # ---------------------------------------------------------------- пароль
    def ask_password(self):
        dlg = tk.Toplevel(self)
        dlg.title("Экстренный доступ")
        dlg.configure(bg=PANEL)
        dlg.geometry("380x180")
        dlg.grab_set()
        tk.Label(
            dlg, text="Введите пароль экстренного доступа,\nчтобы немедленно включить Wi-Fi:",
            bg=PANEL, font=("Segoe UI", 11), justify="center",
        ).pack(pady=(18, 8))
        var = tk.StringVar()
        ent = tk.Entry(dlg, textvariable=var, show="•", font=("Segoe UI", 14), justify="center")
        ent.pack(pady=4)
        ent.focus_set()
        row = tk.Frame(dlg, bg=PANEL)
        row.pack(pady=12)

        def try_pass(event=None):
            if var.get().strip() == config.EMERGENCY_PASSWORD:
                self.session.unlock("password")
                self._refresh_chrome()
                messagebox.showinfo(
                    "Wi-Fi включён",
                    "Верный пароль! Wi-Fi включён.\n"
                    "(Чтобы снова заблокировать — нажмите «Заблокировать Wi-Fi».)",
                    parent=dlg,
                )
                dlg.destroy()
                self.frames["HomeFrame"].refresh()
            else:
                messagebox.showerror("Ошибка", "Неверный пароль.", parent=dlg)
                var.set("")

        ttk.Button(row, text="Включить Wi-Fi", command=try_pass).pack(side="left", padx=6)
        ttk.Button(row, text="Отмена", command=dlg.destroy).pack(side="left", padx=6)
        ent.bind("<Return>", try_pass)

    # ---------------------------------------------------------------- чтение
    def start_reading(self):
        if self.reading:
            return
        words = self.session.matcher.words
        try:
            if self.demo:
                # демо: распознавание подставляет слова книги (с шумом)
                remaining = words[self.session.matcher.pos:]
                self.recognizer = stt.FakeRecognizer(
                    remaining, words_per_second=120, noise_every=15,
                    on_words=self._on_words,
                )
            else:
                self.recognizer = stt.VoskRecognizer(
                    on_words=self._on_words,
                    on_partial=self.frames["ReadFrame"].set_partial,
                )
            self.recognizer.start()
            self.reading = True
            self.frames["ReadFrame"].set_status("Идёт распознавание речи…")
        except RuntimeError as e:
            messagebox.showerror("Не удалось начать чтение", str(e))
            self.recognizer = None

    def stop_reading(self):
        if self.recognizer:
            self.recognizer.stop()
            self.recognizer = None
        self.reading = False

    def _on_words(self, words):
        res = self.session.matcher.feed(words)
        self.session.save()
        fr = self.frames["ReadFrame"]
        fr.on_progress(res)
        if self.session.reading_done() and not self.session.quiz_passed():
            self.after(0, lambda: messagebox.showinfo(
                "50 страниц прочитаны!",
                "Отлично! Вы прочитали 50 страниц вслух.\nТеперь можно пройти лёгкий тест.",
            ))
        if res.wrong_book:
            fr.warn_wrong_book()

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
        self.frames["QuizFrame"].load()
        self.show("QuizFrame")

    def finish_quiz(self):
        q = self.quiz
        if q.passed():
            self.session.lib.set("quiz_passed", 1)
            self.session.unlock("quiz")
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


class HomeFrame(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        tk.Label(self, text="Книжный страж", bg=BG, fg=GREEN,
                 font=("Segoe UI", 22, "bold")).pack(pady=(24, 4))
        tk.Label(
            self,
            text="Wi-Fi откроется, когда вы прочитаете 50 страниц книги вслух\n"
                 "и пройдёте лёгкий тест по прочитанному",
            bg=BG, fg=DARK, font=("Segoe UI", 12), justify="center",
        ).pack(pady=(0, 16))

        card = tk.Frame(self, bg=PANEL, highlightbackground="#d9d2bd", highlightthickness=1)
        card.pack(padx=40, fill="x")
        self.lbl_book = tk.Label(card, text="", bg=PANEL, fg=DARK,
                                 font=("Segoe UI", 15, "bold"), wraplength=840)
        self.lbl_book.pack(pady=(16, 2))
        self.lbl_tag = tk.Label(card, text="", bg=PANEL, fg="#777", font=("Segoe UI", 10))
        self.lbl_tag.pack()
        self.lbl_prog = tk.Label(card, text="", bg=PANEL, fg=DARK, font=("Segoe UI", 12))
        self.lbl_prog.pack(pady=(12, 4))
        self.bar = ttk.Progressbar(card, length=520, mode="determinate")
        self.bar.pack(pady=(4, 6))
        self.lbl_note = tk.Label(card, text="", bg=PANEL, fg="#777", font=("Segoe UI", 9))
        self.lbl_note.pack(pady=(0, 14))

        btns = tk.Frame(self, bg=BG)
        btns.pack(pady=18)
        ttk.Button(btns, text="▶  Начать чтение вслух", style="Big.TButton",
                   command=self._read).grid(row=0, column=0, padx=8, pady=6)
        ttk.Button(btns, text="📝  Пройти тест", style="Big.TButton",
                   command=self.app.start_quiz).grid(row=0, column=1, padx=8, pady=6)
        ttk.Button(btns, text="🔒  Заблокировать Wi-Fi",
                   command=lambda: (self.app.session.relock(), self.app._refresh_chrome(),
                                    self.refresh())).grid(row=1, column=0, padx=8, pady=6)
        ttk.Button(btns, text="🔄  Новый цикл (новая книга)",
                   command=self._new_cycle).grid(row=1, column=1, padx=8, pady=6)

        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        chk = tk.Checkbutton(
            self, text="🚀 Запускать «Книжный страж» автоматически при включении компьютера",
            variable=self.autostart_var, command=self._toggle_autostart,
            bg=BG, fg=DARK, font=("Segoe UI", 11), activebackground=BG,
        )
        chk.pack(pady=(2, 2))
        self.lbl_autostart = tk.Label(self, text="", bg=BG, fg="#777", font=("Segoe UI", 9))
        self.lbl_autostart.pack()

        self.lbl_wifi = tk.Label(self, text="", bg=BG, font=("Segoe UI", 11, "bold"))
        self.lbl_wifi.pack(pady=2)

    def _toggle_autostart(self):
        if self.autostart_var.get():
            ok, info = autostart.enable()
            if not ok:
                self.autostart_var.set(False)
                messagebox.showerror("Автозапуск", f"Не удалось включить автозапуск:\n{info}")
                return
            self.lbl_autostart.config(text=f"Автозапуск: {info}", fg=GREEN)
        else:
            autostart.disable()
            self.lbl_autostart.config(text="Автозапуск выключен", fg="#777")

    def _read(self):
        self.app.start_reading()
        self.app.show("ReadFrame")

    def _new_cycle(self):
        if self.app.session.reading_done() and not self.app.session.quiz_passed():
            if not messagebox.askyesno("Новый цикл", "50 страниц уже прочитаны. "
                                                     "Прогресс сбросится. Продолжить?"):
                return
        self.app.stop_reading()
        self.app.session.new_cycle()
        self.app._refresh_chrome()
        self.refresh()

    def on_show(self):
        self.refresh()

    def refresh(self):
        s = self.app.session
        b = s.book
        self.lbl_book.config(text=f"{b['author']}. «{b['title']}»")
        self.lbl_tag.config(text=b["tagline"])
        done = s.pages_done()
        need = self.app.lib.pages_required()
        self.lbl_prog.config(text=f"Прочитано вслух: {min(done, need)} из {need} страниц")
        self.bar["value"] = min(100.0, 100.0 * done / max(1, need))
        if s.quiz_passed():
            self.lbl_note.config(text="Тест пройден — Wi-Fi открыт. Можно взять новую книгу.")
        elif s.reading_done():
            self.lbl_note.config(text="Чтение завершено! Остался лёгкий тест →")
        else:
            self.lbl_note.config(
                text=f"Читайте вслух текст на экране: слова подсвечиваются зелёным. "
                     f"До теста осталось {max(0, need - done)} стр."
            )
        st = self.app.lib.get("wifi_state", config.WIFI_BLOCKED)
        self.lbl_wifi.config(
            text="● Wi-Fi сейчас ВКЛЮЧЁН" if st == config.WIFI_UNBLOCKED else "● Wi-Fi сейчас ЗАБЛОКИРОВАН",
            fg=GREEN if st == config.WIFI_UNBLOCKED else RED,
        )
        try:
            self.autostart_var.set(autostart.is_enabled())
            self.lbl_autostart.config(
                text=("Автозапуск: " + autostart.describe()) if autostart.is_enabled() else "",
            )
        except Exception:  # noqa: BLE001
            pass


class ReadFrame(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=24, pady=(16, 4))
        self.lbl_page = tk.Label(head, text="", bg=BG, font=("Segoe UI", 13, "bold"))
        self.lbl_page.pack(side="left")
        self.lbl_rec = tk.Label(head, text="●", bg=BG, fg="#bbb", font=("Segoe UI", 14))
        self.lbl_rec.pack(side="right")

        self.lbl_status = tk.Label(self, text="", bg=BG, fg="#777", font=("Segoe UI", 10))
        self.lbl_status.pack(fill="x", padx=24)
        self.lbl_warn = tk.Label(self, text="", bg=BG, fg=RED, font=("Segoe UI", 11, "bold"))
        self.lbl_warn.pack(fill="x", padx=24)

        box = tk.Frame(self, bg=PANEL, highlightbackground="#d9d2bd", highlightthickness=1)
        box.pack(fill="both", expand=True, padx=24, pady=8)
        self.text = tk.Text(box, wrap="word", font=("Georgia", 13), bg=PANEL, fg="#111",
                            padx=18, pady=14, spacing1=2, spacing3=4)
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("done", background="#cde8cd")
        self.text.tag_configure("now", background="#ffe9a8")

        self.lbl_partial = tk.Label(self, text="", bg=BG, fg="#555", font=("Segoe UI", 10, "italic"))
        self.lbl_partial.pack(fill="x", padx=24)

        row = tk.Frame(self, bg=BG)
        row.pack(pady=10)
        ttk.Button(row, text="◀", width=3, command=lambda: self.turn(-1)).pack(side="left", padx=4)
        ttk.Button(row, text="⏸ Пауза", command=self.toggle_pause).pack(side="left", padx=4)
        ttk.Button(row, text="▶", width=3, command=lambda: self.turn(1)).pack(side="left", padx=4)
        ttk.Button(row, text="К тесту", command=self._quiz).pack(side="left", padx=14)
        ttk.Button(row, text="На главную", command=self._home).pack(side="left", padx=4)

        self.page_no = 1
        self.paused = False
        self._blink = False

    # --------- отображение
    def on_show(self):
        s = self.app.session
        self.page_no = s.current_page()
        self.load_page()
        if not self.app.reading and not s.reading_done():
            self.set_status("Нажмите «Начать чтение вслух» на главном экране — или продолжайте: "
                            "кнопка Пауза/старт уже здесь, если микрофон работает")
        if not self.app.reading:
            self.app.start_reading()

    def load_page(self):
        s = self.app.session
        page = s.pages.get(self.page_no)
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        if page:
            self.text.insert("1.0", s.text[page["char_start"]:page["char_end"]])
            self._highlight(page)
        self.text.config(state="normal")
        need = self.app.lib.pages_required()
        total = max(s.pages)
        self.lbl_page.config(
            text=f"Страница {self.page_no} из {total}"
                 + (f"  ·  нужно прочитать {need} стр." if self.page_no <= need else "  ·  сверх программы")
        )

    def _highlight(self, page):
        s = self.app.session
        base_word = page["word_start"]
        for i in range(page["word_start"], page["word_end"]):
            if i + 1 <= s.matcher.pos:
                if i < len(s.word_spans):
                    a, b = s.word_spans[i]
                    rel = a - page["char_start"], b - page["char_start"]
                    if rel[0] >= 0:
                        self.text.tag_add("done", f"1.0+{rel[0]}c", f"1.0+{rel[1]}c")
        # слово «сейчас»
        cur = s.matcher.pos
        if page["word_start"] <= cur < page["word_end"] and cur < len(s.word_spans):
            a, b = s.word_spans[cur]
            self.text.tag_add("now", f"1.0+{a - page['char_start']}c", f"1.0+{b - page['char_start']}c")

    def turn(self, delta):
        s = self.app.session
        total = max(s.pages)
        self.page_no = max(1, min(total, self.page_no + delta))
        self.load_page()

    def toggle_pause(self):
        if self.app.reading:
            self.app.stop_reading()
            self.paused = True
            self.set_status("Пауза — распознавание остановлено")
        else:
            self.paused = False
            self.app.start_reading()

    def _quiz(self):
        self.app.start_quiz()

    def _home(self):
        self.app.stop_reading()
        self.app.show("HomeFrame")

    # --------- события от распознавания
    def set_status(self, t):
        self.lbl_status.config(text=t)

    def set_partial(self, s):
        self.lbl_partial.config(text=f"услышано: {s}…" if s else "")

    def on_progress(self, res):
        s = self.app.session
        self._highlight(s.pages.get(self.page_no) or {})
        # авто-перелистывание, когда страница дочитана
        cur = s.current_page()
        if cur != self.page_no and cur > self.page_no:
            self.page_no = cur
            self.load_page()
        done = s.pages_done()
        need = self.app.lib.pages_required()
        self.set_status(f"Совпало со словами книги: {res.matched}, пропущено: {res.skipped}, "
                        f"не распознано в книге: {res.not_matched}   ·   прочитано {min(done, need)}/{need} стр.")
        if not res.wrong_book:
            self.lbl_warn.config(text="")

    def warn_wrong_book(self):
        self.lbl_warn.config(
            text="⚠ Похоже, вы читаете не ту книгу (или совсем другой текст)! "
                 "Прогресс не засчитывается."
        )

    def pulse(self):
        if self.app.reading:
            self._blink = not self._blink
            self.lbl_rec.config(fg=RED if self._blink else "#e57373")
        else:
            self.lbl_rec.config(fg="#bbb")


class QuizFrame(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self.lbl_head = tk.Label(self, text="Лёгкий тест по прочитанным страницам",
                                 bg=BG, fg=GREEN, font=("Segoe UI", 16, "bold"))
        self.lbl_head.pack(pady=(26, 10))
        card = tk.Frame(self, bg=PANEL, highlightbackground="#d9d2bd", highlightthickness=1)
        card.pack(padx=60, fill="x")
        self.lbl_q = tk.Label(card, text="", bg=PANEL, font=("Segoe UI", 13),
                              wraplength=780, justify="left")
        self.lbl_q.pack(padx=24, pady=(20, 10), anchor="w")
        self.var = tk.IntVar(value=-1)
        self.opts = []
        for i in range(4):
            r = tk.Radiobutton(card, text="", variable=self.var, value=i, bg=PANEL,
                               font=("Segoe UI", 12), anchor="w", justify="left",
                               wraplength=760)
            r.pack(fill="x", padx=28, pady=4, anchor="w")
            self.opts.append(r)
        self.lbl_about = tk.Label(card, text="", bg=PANEL, fg="#888", font=("Segoe UI", 9))
        self.lbl_about.pack(anchor="w", padx=28, pady=(4, 12))
        ttk.Button(self, text="Ответить", style="Big.TButton", command=self._answer).pack(pady=16)
        self.lbl_res = tk.Label(self, text="", bg=BG, font=("Segoe UI", 12))
        self.lbl_res.pack()

    def on_show(self):
        self.load()

    def load(self):
        q = self.app.quiz.current() if self.app.quiz else None
        if q is None:
            return
        n = self.app.quiz.index + 1
        total = len(self.app.quiz.questions)
        self.lbl_head.config(text=f"Вопрос {n} из {total}")
        self.lbl_q.config(text=q["question"])
        for i, opt in enumerate(q["options"]):
            self.opts[i].config(text=opt)
        self.lbl_about.config(text=q["about"])
        self.var.set(-1)

    def _answer(self):
        q = self.app.quiz
        if self.var.get() < 0:
            messagebox.showwarning("Выберите ответ", "Отметьте один из вариантов.")
            return
        q.answer(self.var.get())
        if q.finished():
            self.lbl_res.config(text=q.result_text())
            self.app.finish_quiz()
        else:
            self.load()
