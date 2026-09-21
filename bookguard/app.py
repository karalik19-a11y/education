# -*- coding: utf-8 -*-
"""Графический интерфейс «Книжный страж» (Tkinter, тёмная «премиум» тема).

Всё, что приходит с потока распознавания (слова, «живой» текст, уровень
микрофона), проходит через очередь и применяется в главном потоке Tk —
интерфейс всегда плавный, без «зависаний» и гонок.
"""
import platform
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import config, autostart, protector, stt
from .db import Library
from .matcher import BookMatcher
from .quiz import QuizSession
from .textnorm import words_of, spans_of
from .wifi import WifiController

IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# ------------------------------------------------------------------ тема
T = dict(
    bg="#0d1322", panel="#131a2c", card="#1a2338", card_hi="#24304d",
    line="#2b3a5e", text="#eef3fc", muted="#93a4c7", dim="#5b6c92",
    gold="#e8b64c", gold_hi="#f6d287", teal="#3bd6ab", green="#41cf74",
    red="#ef5350", red_hi="#ff8a80", blue="#5b9bff",
    input_bg="#0e1526", done_bg="#15382c", done_fg="#7fd6a8",
    now_bg="#4a3a15", now_fg="#f6d287",
)

if IS_WIN:
    F = "Segoe UI"
    FS = "Georgia"
elif IS_MAC:
    F = "Helvetica Neue"
    FS = "Times New Roman"
else:
    F = "DejaVu Sans"
    FS = "DejaVu Serif"


def bf(size=11, bold=False):
    return (F, size, "bold") if bold else (F, size)


class Bar:
    """Прогресс-бар на canvas (ровный, тематический)."""

    def __init__(self, parent, width=520, height=10, color=None):
        self.cv = tk.Canvas(parent, width=width, height=height, bg=T["card"],
                            highlightthickness=0)
        self.w, self.h = width, height
        self.color = color or T["gold"]
        self._frac = 0.0

    def pack(self, **kw):
        self.cv.pack(**kw)

    def grid(self, **kw):
        self.cv.grid(**kw)

    def set(self, frac):
        self._frac = max(0.0, min(1.0, frac))
        cv = self.cv
        cv.delete("bar")
        cv.delete("frame")
        w = int(self.w * self._frac)
        if w > 1:
            cv.create_rectangle(0, 0, w, self.h, fill=self.color, outline="",
                                tags="bar")
        cv.create_rectangle(0, 0, self.w, self.h, outline=T["line"], width=1,
                            tags="frame")


# ------------------------------------------------------------------ сессия
class Session:
    """Состояние цикла: книга, прогресс чтения, Wi-Fi."""

    def __init__(self, lib: Library, wifi: WifiController, on_wifi=None):
        self.lib = lib
        self.wifi = wifi
        # on_wifi(op, ok, state) — итог операции над Wi-Fi (из фонового потока)
        self.on_wifi = on_wifi or (lambda op, ok, state: None)
        self.book = None
        self.matcher = None
        self.text = ""
        self.word_spans = []
        self.pages = {}

        bid = lib.get("current_book_id")
        if bid and lib.book(int(bid)):
            self.book = lib.book(int(bid))
            self.bind_book()
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
        self.lib.set("wifi_state", config.WIFI_UNBLOCKED)
        if reason:
            self.lib.set("unlocked_reason", reason)
        # асинхронно: включение адаптера может занять до пары минут,
        # интерфейс не должен «зависать» на это время
        self.wifi.unblock_async(lambda ok, st: self.on_wifi("unblock", ok, st))

    def relock(self):
        self.lib.set("wifi_state", config.WIFI_BLOCKED)
        self.wifi.block_async(lambda ok, st: self.on_wifi("block", ok, st))


# ------------------------------------------------------------------ приложение
class App(tk.Tk):
    def __init__(self, demo=False, simulate_wifi=False):
        super().__init__()
        self.title(f"{config.APP_NAME} — читай книгу вслух, открывай Wi-Fi")
        self.configure(bg=T["bg"])
        self.geometry("1060x760")
        self.minsize(920, 640)
        self.demo = demo

        self.lib = Library()
        self.wifi = WifiController(simulate=simulate_wifi or demo)
        self.session = Session(
            self.lib, self.wifi,
            on_wifi=lambda op, ok, st: self._push("wifi_op", op, ok, st),
        )
        self.session.relock()  # асинхронно — старт не блокируется
        self._wifi_pending("block")

        self.recognizer = None
        self.reading = False
        self.quiz = None
        self._pending_read = False
        self._reading_done_told = False
        self._level = 0.0
        self._model_note = ""

        # устройство звука (из настроек)
        self._mic_name = self.lib.get("stt_device", "")
        self._model_quality = self.lib.get(
            "stt_model",
            config.STT_MODEL_BIG if stt.has_model(config.STT_MODEL_BIG)
            else config.STT_MODEL_FAST,
        )

        self._ui_q = queue.Queue()

        self._build_style()
        self._build_chrome()
        self.container = tk.Frame(self, bg=T["bg"])
        self.container.pack(fill="both", expand=True)
        self.frames = {}
        for F in (HomeFrame, ReadFrame, QuizFrame):
            fr = F(self.container, self)
            self.frames[F.__name__] = fr
            fr.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.show("HomeFrame")

        # фоновые задачи: модель + страж
        if not demo:
            stt.ModelManager.ensure(self._model_quality, on_state=self._model_state)
            self.after(1200, self._ensure_guard)

        self.after(150, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- каркас
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass
        style.configure("TProgressbar", troughcolor=T["card"],
                        background=T["gold"], thickness=10)
        style.configure("TCombobox", fieldbackground=T["input_bg"],
                        background=T["card"], foreground=T["text"],
                        arrowcolor=T["muted"], bordercolor=T["line"],
                        lightcolor=T["card"], darkcolor=T["card"])
        style.map("TCombobox", fieldbackground=[("readonly", T["input_bg"])])

    def _build_chrome(self):
        top = tk.Frame(self, bg=T["panel"], highlightbackground=T["line"],
                       highlightthickness=1)
        top.pack(fill="x")
        tk.Label(top, text="📚", font=(F, 18)).pack(side="left", padx=(14, 2))
        tk.Label(top, text=config.APP_NAME.upper(), bg=T["panel"], fg=T["gold"],
                 font=bf(15, True)).pack(side="left", pady=10)
        tk.Label(top, text=f"v{config.APP_VERSION}", bg=T["panel"], fg=T["dim"],
                 font=bf(9)).pack(side="left", padx=(6, 0), pady=(16, 0))

        self.wifi_badge = tk.Label(
            top, text="  Wi-Fi  ЗАБЛОКИРОВАН  ", bg=T["red"], fg="white",
            font=bf(10, True), padx=10, pady=5,
        )
        self.wifi_badge.pack(side="left", padx=18, pady=10)

        self.book_label = tk.Label(top, text="", bg=T["panel"], fg=T["muted"],
                                   font=bf(11))
        self.book_label.pack(side="left", padx=6)

        if self.demo:
            tk.Label(top, text=" ДЕМО ", bg="#8a6d1d", fg="white",
                     font=bf(9, True), padx=8, pady=5).pack(side="right", padx=8)
        self.btn_uninstall = self._flat_btn(
            top, "Удалить", lambda: self.ask_uninstall(), fg=T["red_hi"])
        self.btn_uninstall.pack(side="right", padx=(4, 4))
        self.btn_emergency = self._flat_btn(
            top, "⚡ Экстренный доступ", self.ask_password, fg=T["gold"])
        self.btn_emergency.pack(side="right", padx=4)
        self.btn_settings = self._flat_btn(top, "⚙", self.open_settings)
        self.btn_settings.pack(side="right", padx=12)

        self.model_note = tk.Label(top, text="", bg=T["panel"], fg=T["dim"],
                                   font=bf(9))
        self.model_note.pack(side="left", padx=8)
        self._refresh_chrome()

    def _flat_btn(self, parent, text, cmd, fg=None, bg=None, bold=False,
                  padx=10, pady=5):
        b = tk.Button(
            parent, text=text, command=cmd,
            bg=bg or T["card"], fg=fg or T["text"],
            activebackground=T["card_hi"], activeforeground=fg or T["text"],
            relief="flat", bd=0, cursor="hand2", font=bf(11, bold),
            padx=padx, pady=pady,
            highlightbackground=T["line"], highlightthickness=1,
        )
        return b

    def _refresh_chrome(self):
        st = self.lib.get("wifi_state", config.WIFI_BLOCKED)
        if st == config.WIFI_UNBLOCKED:
            self.wifi_badge.config(text="  Wi-Fi  ВКЛЮЧЁН  ", bg=T["green"])
        else:
            self.wifi_badge.config(text="  Wi-Fi  ЗАБЛОКИРОВАН  ", bg=T["red"])
        b = self.session.book
        self.book_label.config(text=f"Книга: {b['author']}. «{b['title']}»")

    def show(self, name):
        self.frames[name].tkraise()
        self.frames[name].on_show()

    # ---------------------------------------------------------------- цикл GUI
    def _tick(self):
        try:
            self._drain_ui()
        except Exception:  # noqa: BLE001
            pass
        self.frames["ReadFrame"].pulse()
        self._level *= 0.82  # плавное затухание индикатора
        if self._pending_read:
            if stt.ModelManager.state()["ready"]:
                self._pending_read = False
                self.start_reading()
        self.after(150, self._tick)

    def _drain_ui(self):
        try:
            while True:
                item = self._ui_q.get_nowait()
                kind = item[0]
                if kind == "words":
                    self._on_words(item[1])
                elif kind == "partial":
                    self.frames["ReadFrame"].set_live(item[1])
                elif kind == "level":
                    self._level = max(self._level, item[1])
                elif kind == "state":
                    self._rec_state(item[1], item[2])
                elif kind == "uninstall_log":
                    self._uninstall_log(item[1])
                elif kind == "uninstall_done":
                    self._uninstall_done(item[1])
                elif kind == "model_state":
                    self._apply_model_state(item[1], item[2])
                elif kind == "wifi_op":
                    self._on_wifi_op(item[1], item[2], item[3])
        except queue.Empty:
            pass

    # ---------------- Wi-Fi: состояние операций (async, без «зависания»)
    def _wifi_pending(self, op):
        if op == "unblock":
            self.wifi_badge.config(text="  Wi-Fi  ВКЛЮЧАЮ…  ", bg=T["gold"])
        else:
            self.wifi_badge.config(text="  Wi-Fi  БЛОКИРУЮ…  ", bg=T["gold"])

    def _on_wifi_op(self, op, ok, state):
        if op == "unblock":
            if ok:
                self.wifi_badge.config(text="  Wi-Fi  ВКЛЮЧЁН  ", bg=T["green"])
                msg = "🎉 Wi-Fi включён!" if state == "connected" else \
                    "🎉 Wi-Fi включён — подключение к сети идёт (может занять немного времени)"
                self.toast(msg, "ok")
            else:
                self.wifi_badge.config(text="  Wi-Fi  ЗАБЛОКИРОВАН  ", bg=T["red"])
                self.toast("⚠ Не удалось включить Wi-Fi. Запустите программу "
                           "от имени администратора (ярлык «Книжный страж (админ)»).", "err")
        else:  # block
            if ok:
                self.wifi_badge.config(text="  Wi-Fi  ЗАБЛОКИРОВАН  ", bg=T["red"])
                self.toast("🔒 Wi-Fi заблокирован", "ok")
            else:
                self.wifi_badge.config(text="  Wi-Fi  ВКЛЮЧЁН  ", bg=T["green"])
                self.toast("⚠ Не удалось заблокировать Wi-Fi "
                           "(нужны права администратора).", "err")
        try:
            self.frames["HomeFrame"].refresh()
        except Exception:  # noqa: BLE001
            pass

    def _apply_model_state(self, state, msg):
        if state == "ready":
            self._model_note.config(
                text=f"Модель: {stt.model_quality_name(msg)} — готова")
        elif state == "missing":
            self._model_note.config(text="Модель не найдена — «⚙ Настройки»")
        elif state == "error":
            self._model_note.config(text="Модель: ошибка загрузки")

    def _rec_state(self, state, msg):
        fr = self.frames["ReadFrame"]
        if state == "drop":
            r = self.recognizer
            if r and getattr(r, "dropped", 0) % 20 < 2:
                fr.set_status("⚠ Компьютер не успевает распознавать — часть речи "
                              "может не засчитаться (выберите «быструю» модель в настройках)")
        elif state == "error":
            fr.set_status("⚠ " + msg)
        elif state == "loading":
            self._model_note.config(text=msg)

    def _model_state(self, state, msg):
        # вызывается из фонового потока — только через очередь!
        self._push("model_state", state, msg)

    def _ensure_guard(self):
        def work():
            try:
                if not protector.is_disabled():
                    protector.start_guard_background(log=lambda s: None)
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=work, daemon=True).start()

    def _push(self, *item):
        self._ui_q.put(item)

    # колбэки распознавания (вызываются из потока звука!)
    def _cb_words(self, words):
        self._push("words", words)

    def _cb_partial(self, s):
        self._push("partial", s)

    def _cb_level(self, v):
        self._push("level", v)

    def _cb_state(self, s, m=""):
        self._push("state", s, m)

    def _on_close(self):
        self.stop_reading()
        self.session.save()
        try:
            self.lib.close()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    # ---------------------------------------------------------------- пароль
    def ask_password(self):
        def on_ok(_pw):
            self._wifi_pending("unblock")
            self.session.unlock("password")  # асинхронно
            self._refresh_chrome()
            self.toast("⚡ Пароль принят — включаю Wi-Fi…\n"
                       "(адаптер перезагружается, может занять до пары минут)", "info")
            self.frames["HomeFrame"].refresh()

        PasswordDialog(
            self, "Экстренный доступ",
            "Введите пароль, чтобы немедленно включить Wi-Fi:",
            "⚡ Включить Wi-Fi", on_ok,
        )

    def ask_uninstall(self):
        def on_ok(_pw):
            self._start_uninstall(_pw)

        PasswordDialog(
            self, "Удаление программы",
            "Удаление возможно только по паролю.\n"
            "Будут удалены: программа, резервная копия,\n"
            "автозапуск, ярлыки и защита от удаления.",
            "Удалить программу", on_ok, danger=True,
        )

    def _start_uninstall(self, pw):
        self._uninstall_log_lines = []
        self._uninstall_dlg = UninstallProgress(self)

        def work():
            def log(s):
                self._push("uninstall_log", s)
            ok = protector.run_uninstall(pw, delete_files=False, log=log)
            self._push("uninstall_done", ok)

        threading.Thread(target=work, daemon=True).start()

    def _uninstall_log(self, line):
        dlg = getattr(self, "_uninstall_dlg", None)
        if dlg:
            dlg.append(line)

    def _uninstall_done(self, ok):
        dlg = getattr(self, "_uninstall_dlg", None)
        if ok:
            if dlg:
                dlg.append("Готово. Закрываю приложение, файлы удалятся через пару секунд.")
                dlg.finalize()
            # финальное удаление папки — отдельным фоновым процессом
            protector.spawn_final_wipe()
            self.stop_reading()
            self.destroy()
        elif dlg:
            dlg.append("✗ Удаление не завершено. Повторите попытку.")

    # ---------------------------------------------------------------- чтение
    def _mic_device(self):
        if not self._mic_name:
            return None
        try:
            import sounddevice as sd
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and d["name"] == self._mic_name:
                    return i
        except Exception:  # noqa: BLE001
            pass
        return None

    def start_reading(self):
        if self.reading:
            return
        s = self.session
        fr = self.frames["ReadFrame"]
        try:
            if self.demo:
                remaining = s.matcher.words[s.matcher.pos:]
                self.recognizer = stt.FakeRecognizer(
                    remaining,
                    on_words=self._cb_words, on_partial=self._cb_partial,
                    on_level=self._cb_level, on_state=self._cb_state,
                )
            else:
                st = stt.ModelManager.state()
                model = stt.ModelManager.get_model()
                if model is None:
                    if st["loading"]:
                        self._pending_read = True
                        fr.set_status("Загрузка модели распознавания — старт через пару секунд…")
                        return
                    self._pending_read = False
                    self._model_note.config(text="Модель не найдена — «⚙ Настройки»")
                    messagebox.showerror(
                        "Нет модели распознавания",
                        "Скачайте модель в «⚙ Настройки» или запустите установщик.\n\n"
                        f"Подробности: {st['error']}",
                    )
                    return
                self.recognizer = stt.VoskRecognizer(
                    on_words=self._cb_words, on_partial=self._cb_partial,
                    on_level=self._cb_level, on_state=self._cb_state,
                    model=model, device=self._mic_device(),
                )
            self.recognizer.start()
            self.reading = True
            fr.set_status("🎤 Идёт распознавание — читайте вслух, слова засчитываются сразу")
        except RuntimeError as e:
            self.recognizer = None
            messagebox.showerror("Не удалось начать чтение", str(e))
            self.reading = False

    def stop_reading(self):
        if self.recognizer:
            try:
                self.recognizer.stop()
            except Exception:  # noqa: BLE001
                pass
            self.recognizer = None
        self.reading = False

    def _on_words(self, words):
        res = self.session.matcher.feed(words)
        self.session.save()
        fr = self.frames["ReadFrame"]
        fr.on_progress(res, words)
        if self.session.reading_done() and not self.session.quiz_passed() \
                and not self._reading_done_told:
            self._reading_done_told = True
            self.toast("✅ Страницы прочитаны! Теперь можно пройти тест", "ok")
        if res.wrong_book:
            fr.warn_wrong_book()
        else:
            fr.clear_warn()

    # ---------------------------------------------------------------- тест
    def start_quiz(self):
        if not self.session.quiz_allowed():
            self.toast("Сначала прочитайте нужное число страниц вслух", "warn")
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
            self._wifi_pending("unblock")
            self.session.unlock("quiz")  # асинхронно
            self._refresh_chrome()
            self.toast("🎉 Тест пройден! Включаю Wi-Fi…", "ok")
        else:
            self.toast("Тест не пройден — перечитайте и попробуйте ещё раз", "err")
        self.show("HomeFrame")

    # ---------------------------------------------------------------- настройки
    def open_settings(self):
        SettingsDialog(self)

    # ---------------------------------------------------------------- тосты
    def toast(self, text, kind="info"):
        try:
            t = tk.Toplevel(self)
            t.overrideredirect(True)
            t.configure(bg=T["card"])
            color = {"ok": T["green"], "err": T["red"],
                     "info": T["blue"], "warn": T["gold"]}.get(kind, T["blue"])
            w, h = 460, 58
            x = self.winfo_screenwidth() - w - 28
            y = self.winfo_rooty() + 64
            t.geometry(f"{w}x{h}+{x}+{y}")
            tk.Label(t, text=text, bg=T["card"], fg=color, font=bf(11, True),
                     anchor="w", padx=16, wraplength=w - 32).pack(fill="both", expand=True)
            t.after(4500, t.destroy)
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ главный экран
class HomeFrame(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=T["bg"])
        self.app = app

        tk.Label(self, text="КНИЖНЫЙ СТРАЖ", bg=T["bg"], fg=T["gold"],
                 font=bf(24, True)).pack(pady=(22, 2))
        tk.Label(
            self,
            text="Wi-Fi откроется, когда вы прочитаете книгу вслух и пройдёте лёгкий тест",
            bg=T["bg"], fg=T["muted"], font=bf(12), justify="center",
        ).pack(pady=(0, 14))

        mid = tk.Frame(self, bg=T["bg"])
        mid.pack(padx=40, fill="both", expand=True)

        # кольцо прогресса
        self.ring = tk.Canvas(mid, width=250, height=250, bg=T["bg"],
                              highlightthickness=0)
        self.ring.grid(row=0, column=0, rowspan=2, padx=(0, 24), pady=10)

        # карточка книги
        card = tk.Frame(mid, bg=T["card"], highlightbackground=T["line"],
                        highlightthickness=1)
        card.grid(row=0, column=1, sticky="nsew")
        mid.columnconfigure(1, weight=1)
        mid.rowconfigure(1, weight=1)
        self.lbl_author = tk.Label(card, text="", bg=T["card"], fg=T["muted"],
                                   font=bf(12), anchor="w")
        self.lbl_author.pack(fill="x", padx=20, pady=(16, 0))
        self.lbl_book = tk.Label(card, text="", bg=T["card"], fg=T["text"],
                                 font=bf(20, True), anchor="w", wraplength=620)
        self.lbl_book.pack(fill="x", padx=20)
        self.lbl_tag = tk.Label(card, text="", bg=T["card"], fg=T["dim"],
                                font=(FS, 11, "italic"), anchor="w", wraplength=620)
        self.lbl_tag.pack(fill="x", padx=20, pady=(4, 12))
        self.bar = Bar(card, width=560, height=10)
        self.bar.pack(fill="x", padx=20, pady=(0, 6))
        self.lbl_prog = tk.Label(card, text="", bg=T["card"], fg=T["text"],
                                 font=bf(12), anchor="w")
        self.lbl_prog.pack(fill="x", padx=20)
        self.lbl_note = tk.Label(card, text="", bg=T["card"], fg=T["muted"],
                                 font=bf(11), anchor="w", wraplength=620)
        self.lbl_note.pack(fill="x", padx=20, pady=(8, 16))

        btns = tk.Frame(mid, bg=T["bg"])
        btns.grid(row=2, column=0, columnspan=2, pady=(14, 0))
        self._big_btn(btns, "🎤  Начать чтение вслух", self._read,
                      bg=T["gold"], fg="#1a1205").grid(row=0, column=0, padx=(0, 10), pady=6)
        self._big_btn(btns, "📝  Пройти тест", self.app.start_quiz).grid(
            row=0, column=1, padx=5, pady=6)
        self._big_btn(btns, "🔄  Новый цикл", self._new_cycle).grid(
            row=1, column=0, columnspan=2, padx=5, pady=4)
        row3 = tk.Frame(btns, bg=T["bg"])
        row3.grid(row=2, column=0, columnspan=2, pady=(2, 0))
        self._big_btn(row3, "🔒  Заблокировать Wi-Fi", self._relock).grid(
            row=0, column=0, padx=5)
        self._big_btn(row3, "⚡  Экстренное отключение Wi-Fi", self._relock,
                      fg=T["red_hi"]).grid(row=0, column=1, padx=5)

        self.autostart_var = tk.BooleanVar(value=False)
        chk = tk.Checkbutton(
            self, text="🚀 Запускать «Книжный страж» автоматически при включении компьютера",
            variable=self.autostart_var, command=self._toggle_autostart,
            bg=T["bg"], fg=T["muted"], font=bf(11), activebackground=T["bg"],
            activeforeground=T["text"], selectcolor=T["card"],
        )
        chk.pack(pady=(12, 0))
        self.lbl_autostart = tk.Label(self, text="", bg=T["bg"], fg=T["dim"],
                                      font=bf(9))
        self.lbl_autostart.pack()

        self.lbl_wifi = tk.Label(self, text="", bg=T["bg"], font=bf(11, True))
        self.lbl_wifi.pack(pady=4)

    def _big_btn(self, parent, text, cmd, bg=None, fg=None):
        return tk.Button(
            parent, text=text, command=cmd,
            bg=bg or T["card"], fg=fg or T["text"],
            activebackground=T["card_hi"] if bg is None else T["gold_hi"],
            activeforeground=fg or T["text"],
            relief="flat", bd=0, cursor="hand2", font=bf(13, True),
            padx=20, pady=11,
            highlightbackground=T["line"], highlightthickness=1,
        )

    def _draw_ring(self, done, need):
        cv = self.ring
        cv.delete("all")
        x0, y0, x1, y1 = 16, 16, 234, 234
        cv.create_oval(x0, y0, x1, y1, outline=T["card_hi"], width=16)
        frac = max(0.0, min(1.0, done / max(1, need)))
        if frac > 0:
            cv.create_arc(x0, y0, x1, y1, start=90, extent=-min(359.9, frac * 360),
                          style="arc", outline=T["gold"], width=16)
        cv.create_text(125, 100, text=str(min(done, need)),
                       font=bf(40, True), fill=T["text"])
        cv.create_text(125, 138, text=f"из {need} страниц",
                       font=bf(13), fill=T["muted"])
        cv.create_text(125, 166, text="прочитано вслух",
                       font=bf(10), fill=T["dim"])

    def _toggle_autostart(self):
        if self.autostart_var.get():
            ok, info = autostart.enable(log=lambda s: None)
            if not ok:
                self.autostart_var.set(False)
                messagebox.showerror("Автозапуск", f"Не удалось включить автозапуск:\n{info}")
                return
            self.lbl_autostart.config(text=f"Автозапуск: {info}", fg=T["green"])
        else:
            autostart.disable(log=lambda s: None)
            self.lbl_autostart.config(text="Автозапуск выключен", fg=T["dim"])

    def _read(self):
        self.app.start_reading()
        self.app.show("ReadFrame")

    def _relock(self):
        self.app.stop_reading()
        self.app._wifi_pending("block")
        self.app.session.relock()  # асинхронно
        self.app._refresh_chrome()
        self.refresh()

    def _new_cycle(self):
        if self.app.session.reading_done() and not self.app.session.quiz_passed():
            if not messagebox.askyesno(
                    "Новый цикл", "Чтение уже завершено. Прогресс сбросится. Продолжить?"):
                return
        self.app.stop_reading()
        self.app.session.new_cycle()
        self.app._reading_done_told = False
        self.app._refresh_chrome()
        self.refresh()

    def on_show(self):
        self.refresh()

    def refresh(self):
        s = self.app.session
        b = s.book
        self.lbl_author.config(text=b["author"])
        self.lbl_book.config(text=f"«{b['title']}»")
        self.lbl_tag.config(text=b["tagline"])
        done = s.pages_done()
        need = self.app.lib.pages_required()
        self._draw_ring(done, need)
        self.bar.set(done / max(1, need))
        self.lbl_prog.config(text=f"Прочитано вслух: {min(done, need)} из {need} страниц")
        if s.quiz_passed():
            self.lbl_note.config(text="Тест пройден — Wi-Fi открыт. Можно взять новую книгу.")
        elif s.reading_done():
            self.lbl_note.config(text="Чтение завершено! Остался лёгкий тест → «Пройти тест»")
        else:
            self.lbl_note.config(
                text=f"Читайте вслух текст на экране — слова подсвечиваются, "
                     f"до теста осталось {max(0, need - done)} стр.")
        st = self.app.lib.get("wifi_state", config.WIFI_BLOCKED)
        on = st == config.WIFI_UNBLOCKED
        self.lbl_wifi.config(
            text=("● Wi-Fi сейчас ВКЛЮЧЁН" if on else "● Wi-Fi сейчас ЗАБЛОКИРОВАН"),
            fg=T["green"] if on else T["red"])
        try:
            en = autostart.is_enabled()
            self.autostart_var.set(en)
            self.lbl_autostart.config(
                text=("Автозапуск: " + autostart.describe()) if en else "",
                fg=T["dim"])
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ экран чтения
class ReadFrame(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=T["bg"])
        self.app = app

        head = tk.Frame(self, bg=T["bg"])
        head.pack(fill="x", padx=24, pady=(14, 4))
        self.lbl_page = tk.Label(head, text="", bg=T["bg"], fg=T["text"],
                                 font=bf(13, True))
        self.lbl_page.pack(side="left")
        self.lbl_rec = tk.Label(head, text="●", bg=T["bg"], fg=T["dim"],
                                font=(F, 14))
        self.lbl_rec.pack(side="right")

        # ---- «живая» расшифровка
        live_card = tk.Frame(self, bg=T["card"], highlightbackground=T["line"],
                             highlightthickness=1)
        live_card.pack(fill="x", padx=24, pady=6)
        live_head = tk.Frame(live_card, bg=T["card"])
        live_head.pack(fill="x", padx=16, pady=(10, 0))
        tk.Label(live_head, text="🎤 ВЫ ГОВОРИТЕ", bg=T["card"], fg=T["gold"],
                 font=bf(10, True)).pack(side="left")
        self.meter = tk.Canvas(live_head, width=130, height=14, bg=T["card"],
                               highlightthickness=0)
        self.meter.pack(side="right")
        self.lbl_mic = tk.Label(live_head, text="микрофон", bg=T["card"],
                                fg=T["dim"], font=bf(9))
        self.lbl_mic.pack(side="right", padx=(0, 8))
        self.live_text = tk.Text(live_card, height=3, wrap="word",
                                 font=bf(15), bg=T["card"], fg=T["text"],
                                 padx=16, pady=8, state="disabled", relief="flat")
        self.live_text.pack(fill="x", padx=8, pady=(2, 10))
        self.live_text.tag_configure("soft", foreground=T["dim"],
                                     font=(F, 15, "italic"))
        self.live_text.tag_configure("hint", foreground=T["dim"], font=(F, 11))

        # ---- подтверждённые слова
        hist_card = tk.Frame(self, bg=T["card"], highlightbackground=T["line"],
                             highlightthickness=1)
        hist_card.pack(fill="x", padx=24, pady=6)
        tk.Label(hist_card, text="ПОДТВЕРЖДЕНО (зелёное — из книги, красное — мимо)",
                 bg=T["card"], fg=T["dim"], font=bf(9), anchor="w"
                 ).pack(fill="x", padx=16, pady=(8, 0))
        self.hist_text = tk.Text(hist_card, height=2, wrap="word",
                                 font=bf(11), bg=T["card"], fg=T["muted"],
                                 padx=16, pady=6, state="disabled", relief="flat")
        self.hist_text.pack(fill="x", padx=8, pady=(0, 8))
        self.hist_text.tag_configure("good", foreground=T["teal"])
        self.hist_text.tag_configure("bad", foreground=T["red_hi"])
        self.hist_text.tag_configure("neutral", foreground=T["dim"])

        self.lbl_warn = tk.Label(self, text="", bg=T["bg"], fg=T["red_hi"],
                                 font=bf(11, True))
        self.lbl_warn.pack(fill="x", padx=24)
        self.lbl_status = tk.Label(self, text="", bg=T["bg"], fg=T["muted"],
                                   font=bf(10), anchor="w")
        self.lbl_status.pack(fill="x", padx=24)

        # ---- текст страницы
        box = tk.Frame(self, bg=T["card"], highlightbackground=T["line"],
                       highlightthickness=1)
        box.pack(fill="both", expand=True, padx=24, pady=6)
        self.text = tk.Text(box, wrap="word", font=(FS, 14), bg=T["card"],
                            fg=T["text"], padx=18, pady=14, spacing1=2, spacing3=4)
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("done", background=T["done_bg"],
                                foreground=T["done_fg"])
        self.text.tag_configure("now", background=T["now_bg"],
                                foreground=T["now_fg"])

        row = tk.Frame(self, bg=T["bg"])
        row.pack(pady=10, padx=24)
        stop = tk.Button(row, text="⏹ Стоп", command=self._stop, bg=T["red"],
                         fg="white", activebackground=T["red_hi"], activeforeground="white",
                         relief="flat", bd=0, cursor="hand2", font=bf(12, True),
                         padx=16, pady=8)
        stop.pack(side="left", padx=(0, 6))
        self.btn_pause = tk.Button(row, text="⏸ Пауза", command=self.toggle_pause,
                                   bg=T["card"], fg=T["text"], activebackground=T["card_hi"],
                                   relief="flat", bd=0, cursor="hand2", font=bf(12, True),
                                   padx=14, pady=8, highlightbackground=T["line"],
                                   highlightthickness=1)
        self.btn_pause.pack(side="left", padx=6)
        tk.Button(row, text="◀", width=3, command=lambda: self.turn(-1),
                  bg=T["card"], fg=T["text"], activebackground=T["card_hi"],
                  relief="flat", bd=0, cursor="hand2", font=bf(12),
                  highlightbackground=T["line"], highlightthickness=1
                  ).pack(side="left", padx=6)
        tk.Button(row, text="▶", width=3, command=lambda: self.turn(1),
                  bg=T["card"], fg=T["text"], activebackground=T["card_hi"],
                  relief="flat", bd=0, cursor="hand2", font=bf(12),
                  highlightbackground=T["line"], highlightthickness=1
                  ).pack(side="left", padx=6)
        self.btn_quiz = tk.Button(row, text="📝 К тесту", command=self._quiz,
                                  bg=T["card"], fg=T["gold"], activebackground=T["card_hi"],
                                  relief="flat", bd=0, cursor="hand2", font=bf(12, True),
                                  padx=14, pady=8, highlightbackground=T["line"],
                                  highlightthickness=1)
        self.btn_quiz.pack(side="left", padx=10)
        tk.Button(row, text="На главную", command=self._home,
                  bg=T["card"], fg=T["muted"], activebackground=T["card_hi"],
                  relief="flat", bd=0, cursor="hand2", font=bf(12),
                  padx=14, pady=8, highlightbackground=T["line"],
                  highlightthickness=1).pack(side="right", padx=6)
        self.lbl_stats = tk.Label(row, text="", bg=T["bg"], fg=T["dim"],
                                  font=bf(10))
        self.lbl_stats.pack(side="right", padx=8)

        self.page_no = 1
        self.paused = False
        self._blink = False
        self._history = []
        self._live = ""
        self._last_live_ts = 0.0

    # --------- отображение
    def on_show(self):
        s = self.app.session
        self.page_no = s.current_page()
        self.load_page()
        self._history = []
        self._live = ""
        self._rebuild_live(hint="Читайте вслух текст ниже — здесь появится всё, что вы говорите")
        if not self.app.reading and not s.reading_done():
            self.set_status("Микрофон готов. Нажмите «⏸ Пауза»/старт здесь "
                            "или начните с главного экрана.")
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
        self.text.config(state="disabled")
        need = self.app.lib.pages_required()
        total = max(s.pages)
        self.lbl_page.config(
            text=f"Страница {self.page_no} из {total}"
                 + (f"   ·   нужно {need} стр." if self.page_no <= need
                    else "   ·   сверх программы")
        )

    def _highlight(self, page):
        s = self.app.session
        if not page:
            return
        for i in range(page["word_start"], page["word_end"]):
            if i + 1 <= s.matcher.pos and i < len(s.word_spans):
                a, b = s.word_spans[i]
                rel0, rel1 = a - page["char_start"], b - page["char_start"]
                if rel0 >= 0:
                    self.text.tag_add("done", f"1.0+{rel0}c", f"1.0+{rel1}c")
        cur = s.matcher.pos
        if page["word_start"] <= cur < page["word_end"] and cur < len(s.word_spans):
            a, b = s.word_spans[cur]
            self.text.tag_add("now", f"1.0+{a - page['char_start']}c",
                              f"1.0+{b - page['char_start']}c")
            try:
                self.text.see(f"1.0+{a - page['char_start']}c")
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
            self.paused = True
            self.set_status("⏸ Пауза — распознавание остановлено")
            self.btn_pause.config(text="▶ Старт")
        else:
            self.paused = False
            self.btn_pause.config(text="⏸ Пауза")
            self.app.start_reading()

    def _stop(self):
        self.app.stop_reading()
        self.paused = True
        self.btn_pause.config(text="▶ Старт")
        self.set_status("Остановлено. «▶ Старт» — продолжить чтение")

    def _quiz(self):
        self.app.start_quiz()

    def _home(self):
        self.app.stop_reading()
        self.app.show("HomeFrame")

    # --------- события от распознавания
    def set_status(self, t):
        self.lbl_status.config(text=t)

    def set_live(self, s):
        self._live = s
        now = time.time()
        if now - self._last_live_ts < 0.12 and s:
            return  # дросселируем перерисовку
        self._last_live_ts = now
        self._rebuild_live()

    def _rebuild_live(self, hint=None):
        t = self.live_text
        t.config(state="normal")
        t.delete("1.0", "end")
        hist = self._history[-60:]
        if not hist and not self._live and hint:
            t.insert("end", hint, "hint")
            t.config(state="disabled")
            return
        for w, flag in hist:
            tag = "good" if flag is True else ("bad" if flag is False else "neutral")
            t.insert("end", w + " ", tag)
        if self._live:
            t.insert("end", "  ▸ ", "soft")
            t.insert("end", self._live + " …", "soft")
        t.config(state="disabled")
        t.see("end")

    def on_progress(self, res, words):
        for w, flag in zip(words, res.per_word):
            self._history.append((w, flag))
        if len(self._history) > 240:
            self._history = self._history[-240:]
        self._rebuild_live()

        ht = self.hist_text
        ht.config(state="normal")
        ht.delete("1.0", "end")
        for w, flag in self._history[-200:]:
            tag = "good" if flag is True else ("bad" if flag is False else "neutral")
            ht.insert("end", w + " ", tag)
        ht.config(state="disabled")
        ht.see("end")

        s = self.app.session
        page = s.pages.get(self.page_no)
        if page:
            self._highlight(page)
        cur = s.current_page()
        if cur != self.page_no and cur > self.page_no:
            self.page_no = cur
            self.load_page()
        done = s.pages_done()
        need = self.app.lib.pages_required()
        self.lbl_stats.config(
            text=f"совпало {res.matched} · мимо {res.not_matched} · "
                 f"стр. {min(done, need)}/{need}")
        if not res.wrong_book:
            self.lbl_warn.config(text="")

    def warn_wrong_book(self):
        self.lbl_warn.config(
            text="⚠ Похоже, вы читаете не ту книгу (или совсем другой текст) — "
                 "прогресс не засчитывается")

    def clear_warn(self):
        self.lbl_warn.config(text="")

    def pulse(self):
        if self.app.reading:
            self._blink = not self._blink
            self.lbl_rec.config(fg=T["red"] if self._blink else T["red_hi"])
        else:
            self.lbl_rec.config(fg=T["dim"])
        # индикатор уровня микрофона
        m = self.meter
        m.delete("all")
        m.create_rectangle(0, 1, 130, 13, outline=T["line"], width=1)
        lv = max(0.0, min(1.0, self.app._level))
        if lv > 0.02:
            m.create_rectangle(2, 3, int(128 * lv), 11,
                               fill=T["gold"] if lv < 0.85 else T["red_hi"],
                               outline="")


# ------------------------------------------------------------------ тест
class QuizFrame(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=T["bg"])
        self.app = app
        tk.Label(self, text="Лёгкий тест по прочитанным страницам",
                 bg=T["bg"], fg=T["gold"], font=bf(18, True)).pack(pady=(26, 10))
        self.lbl_head = tk.Label(self, text="", bg=T["bg"], fg=T["muted"],
                                 font=bf(11))
        self.lbl_head.pack()

        card = tk.Frame(self, bg=T["card"], highlightbackground=T["line"],
                        highlightthickness=1)
        card.pack(padx=70, fill="x")
        self.lbl_q = tk.Label(card, text="", bg=T["card"], fg=T["text"],
                              font=bf(14), wraplength=760, justify="left")
        self.lbl_q.pack(padx=26, pady=(20, 14), anchor="w")
        self.btns = []
        for i, letter in enumerate("ABCD"):
            b = tk.Button(card, text=f"   {letter}.  ", command=lambda i=i: self._select(i),
                          bg=T["card_hi"], fg=T["text"], activebackground=T["card"],
                          relief="flat", bd=0, cursor="hand2", font=bf(13),
                          anchor="w", justify="left", wraplength=700, padx=14, pady=10,
                          highlightbackground=T["line"], highlightthickness=1)
            b.pack(fill="x", padx=22, pady=5, anchor="w")
            self.btns.append(b)
        self.lbl_about = tk.Label(card, text="", bg=T["card"], fg=T["dim"],
                                  font=bf(9))
        self.lbl_about.pack(anchor="w", padx=26, pady=(6, 14))

        self.btn_answer = tk.Button(self, text="Ответить", command=self._answer,
                                    bg=T["gold"], fg="#1a1205",
                                    activebackground=T["gold_hi"], activeforeground="#1a1205",
                                    relief="flat", bd=0, cursor="hand2",
                                    font=bf(14, True), padx=28, pady=10)
        self.btn_answer.pack(pady=14)

        self.lbl_res = tk.Label(self, text="", bg=T["bg"], fg=T["text"],
                                font=bf(14, True), justify="center", wraplength=700)
        self.lbl_res.pack(pady=6)
        self.btn_done = tk.Button(self, text="Готово", command=self._done,
                                  bg=T["card"], fg=T["green"], activebackground=T["card_hi"],
                                  relief="flat", bd=0, cursor="hand2", font=bf(13, True),
                                  padx=24, pady=9, highlightbackground=T["line"],
                                  highlightthickness=1)
        self.btn_done.pack_forget()

        self.selected = -1

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
            self.btns[i].config(text=f"   {chr(65 + i)}.  {opt}")
        self.lbl_about.config(text=q["about"])
        self.selected = -1
        self._paint()

    def _select(self, i):
        self.selected = i
        self._paint()

    def _paint(self):
        for i, b in enumerate(self.btns):
            if i == self.selected:
                b.config(bg=T["now_bg"], fg=T["now_fg"])
            else:
                b.config(bg=T["card_hi"], fg=T["text"])

    def _answer(self):
        q = self.app.quiz
        if self.selected < 0:
            self.app.toast("Выберите один из вариантов", "warn")
            return
        q.answer(self.selected)
        if q.finished():
            passed = q.passed()
            self.lbl_res.config(
                text=("🎉  " if passed else "😕  ") + q.result_text(),
                fg=T["green"] if passed else T["red_hi"],
            )
            self.btn_done.pack(pady=10)
            for b in self.btns:
                b.config(state="disabled")
            self.btn_answer.config(state="disabled")
        else:
            self.load()

    def _done(self):
        self.app.finish_quiz()


# ------------------------------------------------------------------ диалоги
class PasswordDialog(tk.Toplevel):
    def __init__(self, parent, title, prompt, ok_text, on_ok, danger=False):
        super().__init__(parent)
        self.on_ok_ref = on_ok
        self.title(title)
        self.configure(bg=T["card"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.geometry("460x250")
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - 460) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - 250) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

        tk.Label(self, text=title, bg=T["card"], fg=T["gold"],
                 font=bf(15, True)).pack(pady=(20, 6))
        tk.Label(self, text=prompt, bg=T["card"], fg=T["muted"], font=bf(11),
                 justify="center").pack(pady=(0, 10))
        self.var = tk.StringVar()
        ent = tk.Entry(self, textvariable=self.var, show="•", font=bf(15),
                       justify="center", bg=T["input_bg"], fg=T["text"],
                       insertbackground=T["text"], relief="flat")
        ent.pack(padx=40, fill="x", ipady=6)
        ent.focus_set()

        row = tk.Frame(self, bg=T["card"])
        row.pack(pady=16)
        ok_color = T["red"] if danger else T["gold"]
        ok_fg = "white" if danger else "#1a1205"
        tk.Button(row, text=ok_text, command=self._ok, bg=ok_color, fg=ok_fg,
                  activebackground=T["card_hi"], activeforeground=ok_fg,
                  relief="flat", bd=0, cursor="hand2", font=bf(12, True),
                  padx=18, pady=8).pack(side="left", padx=6)
        tk.Button(row, text="Отмена", command=self.destroy, bg=T["card_hi"],
                  fg=T["muted"], activebackground=T["card"], relief="flat", bd=0,
                  cursor="hand2", font=bf(12), padx=18, pady=8).pack(side="left", padx=6)
        ent.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

    def _ok(self):
        pw = self.var.get().strip()
        if protector.verify_password(pw):
            cb = self.on_ok_ref
            self.destroy()
            cb(pw)
        else:
            self.var.set("")
            if getattr(self, "lbl_err", None) is None:
                self.lbl_err = tk.Label(self, text="Неверный пароль", fg=T["red_hi"],
                                        bg=T["card"], font=bf(10, True))
                self.lbl_err.pack()
            self.lbl_err.config(text="Неверный пароль — попробуйте ещё раз")


class UninstallProgress(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Удаление программы")
        self.configure(bg=T["card"])
        self.geometry("560x300")
        self.transient(parent)
        self.resizable(False, False)
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - 560) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - 300) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        tk.Label(self, text="Удаление «Книжного стража»", bg=T["card"],
                 fg=T["gold"], font=bf(14, True)).pack(pady=(18, 8))
        self.txt = tk.Text(self, width=62, height=10, bg=T["input_bg"],
                           fg=T["text"], font=("Consolas", 10), relief="flat",
                           state="disabled", padx=12, pady=10)
        self.txt.pack(padx=16)
        self.lbl = tk.Label(self, text="Выполняется…", bg=T["card"], fg=T["muted"],
                            font=bf(11))
        self.lbl.pack(pady=10)
        self.protocol("WM_DELETE_WINDOW", lambda: None)

    def append(self, line):
        self.txt.config(state="normal")
        self.txt.insert("end", line + "\n")
        self.txt.see("end")
        self.txt.config(state="disabled")

    def finalize(self):
        self.lbl.config(text="Завершено", fg=T["green"])


# ------------------------------------------------------------------ настройки
class SettingsDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Настройки — «Книжный страж»")
        self.configure(bg=T["card"])
        self.resizable(False, False)
        self.transient(parent)
        self.app = parent
        self.geometry("600x640")
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - 600) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - 640) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

        tk.Label(self, text="⚙  НАСТРОЙКИ", bg=T["card"], fg=T["gold"],
                 font=bf(16, True)).pack(pady=(18, 4))

        # --- модель
        box = tk.Frame(self, bg=T["panel"], highlightbackground=T["line"],
                       highlightthickness=1)
        box.pack(fill="x", padx=20, pady=8)
        tk.Label(box, text="Модель распознавания речи", bg=T["panel"],
                 fg=T["text"], font=bf(12, True)).pack(anchor="w", padx=16, pady=(12, 4))
        self.model_var = tk.StringVar(value=self.app._model_quality)
        r1 = tk.Radiobutton(
            box, text="Быстрая (~33 МБ) — меньше ошибок, для слабых компьютеров",
            value=config.STT_MODEL_FAST, variable=self.model_var,
            bg=T["panel"], fg=T["muted"], activebackground=T["panel"],
            activeforeground=T["text"], selectcolor=T["card"], font=bf(11), anchor="w")
        r1.pack(fill="x", padx=20)
        r2 = tk.Radiobutton(
            box, text="Максимальная точность (~1.4 ГБ) — минимум ошибок в словах",
            value=config.STT_MODEL_BIG, variable=self.model_var,
            bg=T["panel"], fg=T["muted"], activebackground=T["panel"],
            activeforeground=T["text"], selectcolor=T["card"], font=bf(11), anchor="w")
        r2.pack(fill="x", padx=20, pady=(0, 4))
        row = tk.Frame(box, bg=T["panel"])
        row.pack(fill="x", padx=16, pady=(2, 12))
        self.btn_dl = tk.Button(row, text="⬇ Скачать выбранную модель",
                                command=self._download, bg=T["gold"], fg="#1a1205",
                                activebackground=T["gold_hi"], activeforeground="#1a1205",
                                relief="flat", bd=0, cursor="hand2", font=bf(12, True),
                                padx=16, pady=7)
        self.btn_dl.pack(side="left")
        self.dl_state = tk.Label(row, text="", bg=T["panel"], fg=T["dim"],
                                 font=bf(10))
        self.dl_state.pack(side="left", padx=12)
        self.dl_bar = Bar(box, width=520, height=8, color=T["blue"])
        self.dl_bar.pack(fill="x", padx=16)

        # --- микрофон
        box2 = tk.Frame(self, bg=T["panel"], highlightbackground=T["line"],
                        highlightthickness=1)
        box2.pack(fill="x", padx=20, pady=8)
        tk.Label(box2, text="Микрофон", bg=T["panel"], fg=T["text"],
                 font=bf(12, True)).pack(anchor="w", padx=16, pady=(12, 6))
        self.dev_var = tk.StringVar()
        self.devices = ["(основное устройство)"]
        self.devices += stt.list_input_devices()
        self.dev_var.set(self.app._mic_name or self.devices[0])
        self.dev_box = ttk.Combobox(box2, textvariable=self.dev_var,
                                    values=self.devices, state="readonly",
                                    font=bf(11), width=64)
        self.dev_box.pack(fill="x", padx=16, pady=(0, 8))
        row2 = tk.Frame(box2, bg=T["panel"])
        row2.pack(fill="x", padx=16, pady=(0, 12))
        self.btn_test = tk.Button(row2, text="Проверить микрофон (3 сек)",
                                  command=self._test_mic, bg=T["card"], fg=T["text"],
                                  activebackground=T["card_hi"], relief="flat", bd=0,
                                  cursor="hand2", font=bf(11, True), padx=14, pady=7,
                                  highlightbackground=T["line"], highlightthickness=1)
        self.btn_test.pack(side="left")
        self.mic_state = tk.Label(row2, text="", bg=T["panel"], fg=T["dim"],
                                  font=bf(10))
        self.mic_state.pack(side="left", padx=12)

        # --- состояние
        box3 = tk.Frame(self, bg=T["panel"], highlightbackground=T["line"],
                        highlightthickness=1)
        box3.pack(fill="x", padx=20, pady=8)
        tk.Label(box3, text="Состояние программы", bg=T["panel"], fg=T["text"],
                 font=bf(12, True)).pack(anchor="w", padx=16, pady=(12, 6))
        lines = [
            f"Автозапуск приложения: {autostart.describe()}",
            f"Страж (защита от удаления): {'включён' if not protector.is_disabled() else 'ВЫКЛЮЧЕН'}",
            f"Модель: {stt.model_quality_name(self.app._model_quality)} — "
            f"{'установлена' if stt.has_model(self.app._model_quality) else 'НЕ найдена'}",
        ]
        for ln in lines:
            tk.Label(box3, text=ln, bg=T["panel"], fg=T["muted"], font=bf(10),
                     anchor="w").pack(fill="x", padx=18, pady=2)
        tk.Label(box3, text="", bg=T["panel"]).pack(pady=(4, 10))

        row3 = tk.Frame(self, bg=T["card"])
        row3.pack(pady=12)
        tk.Button(row3, text="Сохранить", command=self._save, bg=T["gold"],
                  fg="#1a1205", activebackground=T["gold_hi"], activeforeground="#1a1205",
                  relief="flat", bd=0, cursor="hand2", font=bf(13, True),
                  padx=24, pady=8).pack(side="left", padx=8)
        tk.Button(row3, text="Закрыть", command=self.destroy, bg=T["card_hi"],
                  fg=T["muted"], activebackground=T["card"], relief="flat", bd=0,
                  cursor="hand2", font=bf(13), padx=24, pady=8).pack(side="left", padx=8)

        # события из фонового потока (скачивание) — только через очередь
        self._q = queue.Queue()
        self.after(100, self._drain)

    def _drain(self):
        try:
            while True:
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "progress":
                    done, total = item[1], item[2]
                    if total:
                        self.dl_bar.set(done / total)
                        self.dl_state.config(
                            text=f"{done / 1048576:.0f} / {total / 1048576:.0f} МБ")
                    else:
                        self.dl_state.config(text=f"{done / 1048576:.0f} МБ…")
                elif kind == "log":
                    self.dl_state.config(text=item[1])
                elif kind == "done":
                    self._download_done(item[1], item[2], item[3])
        except queue.Empty:
            pass
        try:
            if self.winfo_exists():
                self.after(100, self._drain)
        except tk.TclError:
            pass

    def _download(self):
        q = self.model_var.get()
        if stt.has_model(q):
            self.dl_state.config(text=f"модель «{stt.model_quality_name(q)}» уже установлена ✓")
            return
        self.btn_dl.config(state="disabled")
        stt.ModelManager.reset()

        def progress(done, total):
            self._q.put(("progress", done, total))

        def work():
            try:
                stt.download_model(
                    q, progress_cb=progress,
                    log=lambda s: self._q.put(("log", s)))
                self._q.put(("done", True, q, ""))
            except Exception as e:  # noqa: BLE001
                self._q.put(("done", False, q, str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _download_done(self, ok, q, err=""):
        self.btn_dl.config(state="normal")
        if ok:
            self.dl_bar.set(1.0)
            self.dl_state.config(text=f"✓ модель «{stt.model_quality_name(q)}» установлена")
            stt.ModelManager.ensure(self.app._model_quality,
                                    on_state=self.app._model_state)
        else:
            self.dl_bar.set(0.0)
            self.dl_state.config(text=f"✗ ошибка: {err[:90]}")

    def _test_mic(self):
        self.btn_test.config(state="disabled")
        self.mic_state.config(text="слушаю 3 секунды…")
        import sounddevice as sd

        levels = []

        def cb(indata, frames, t, status):
            n = len(indata) // 2
            if n < 8:
                return
            step = max(1, n // 100)
            s = 0
            c = 0
            for i in range(0, n * 2 - 1, step * 2):
                v = int.from_bytes(indata[i:i + 2], "little", signed=True)
                s += v * v
                c += 1
            levels.append((s / max(1, c)) ** 0.5 / 32768.0)

        try:
            stream = sd.RawInputStream(samplerate=config.SAMPLE_RATE,
                                       blocksize=config.BLOCK_SIZE, dtype="int16",
                                       channels=1, callback=cb)
            stream.start()
            self.after(3000, lambda: self._test_done(stream, levels))
        except Exception as e:  # noqa: BLE001
            self.mic_state.config(text=f"✗ нет доступа к микрофону: {str(e)[:60]}")
            self.btn_test.config(state="normal")

    def _test_done(self, stream, levels):
        try:
            stream.stop()
            stream.close()
        except Exception:  # noqa: BLE001
            pass
        avg = sum(levels) / max(1, len(levels)) if levels else 0.0
        if avg > 0.02:
            self.mic_state.config(text=f"✓ микрофон работает (уровень {avg:.2f})")
        else:
            self.mic_state.config(text="✗ тишина — проверьте, что микрофон включён")
        self.btn_test.config(state="normal")

    def _save(self):
        q = self.model_var.get()
        self.app.lib.set("stt_model", q)
        dev = self.dev_var.get()
        self.app.lib.set("stt_device", "" if dev == self.devices[0] else dev)
        self.app._model_quality = q
        self.app._mic_name = "" if dev == self.devices[0] else dev
        stt.ModelManager.ensure(self.app._model_quality,
                                on_state=self.app._model_state)
        self.app.toast("Настройки сохранены", "ok")
        self.destroy()
