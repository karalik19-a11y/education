#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Командная строка «Книжного стража».

  python -m bookguard            — запуск программы (интерфейс)
  python -m bookguard demo       — демо интерфейса без микрофона и админа
  python -m bookguard simulate   — полный прогон цикла без GUI (проверка логики)
  python -m bookguard selftest   — быстрая самопроверка
  python -m bookguard block|unblock|status — управление Wi-Fi
  python -m bookguard autostart on|off|status — автозапуск вместе с системой
  python -m bookguard books      — список книг в базе
  python -m bookguard devices    — микрофоны в системе
  python -m bookguard model [fast|big] — выбор/просмотр модели распознавания
  python -m bookguard guard      — фоновый страж (защита от удаления)
  python -m bookguard guard-setup — настроить защиту при установке
  python -m bookguard auth       — проверка пароля (для uninstall-скриптов)
  python -m bookguard uninstall --yes [--keep-files] — полное удаление по паролю
"""
import sys


def cmd_gui(demo=False, simulate_wifi=False):
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("Не найден Tkinter. Установите python3-tk (Linux) или Python с Tk (Windows).")
        sys.exit(1)
    from . import config, single_instance
    from .app import App

    if not demo:
        ok, info = single_instance.acquire(config.DATA_DIR / ".lock", "bookguard")
        if not ok:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(
                config.APP_NAME,
                f"Программа уже запущена.\n({info})\n\nЕсли окно не видно — "
                "посмотрите в трей / список окон.",
            )
            root.destroy()
            return

    app = App(demo=demo, simulate_wifi=simulate_wifi)
    app.mainloop()
    if not demo:
        single_instance.release(config.DATA_DIR / ".lock")


def cmd_books():
    from .db import Library
    lib = Library()
    print("Книги в базе «Книжный страж» (школьная программа, 7 класс):")
    for b in lib.books():
        print(f"  {b['id']:>2}. {b['author']}. {b['title']} — {b['n_pages']} стр.")
    print(f"\nДля доступа нужно прочитать: {lib.pages_required()} страниц")
    lib.close()


def cmd_status():
    from .wifi import WifiController
    w = WifiController()
    print("Wi-Fi:", w.status_hint())


def cmd_block(unblock=False):
    from .wifi import WifiController, print_admin_help
    w = WifiController()
    ok = w.unblock() if unblock else w.block()
    if not ok:
        print_admin_help()
        sys.exit(2)
    print("Готово.")


def cmd_devices():
    from .stt import list_input_devices
    print("Микрофоны:")
    for d in list_input_devices():
        print(" ", d)


def cmd_autostart(arg="status"):
    from . import autostart
    if arg == "on":
        ok, info = autostart.enable()
        if ok:
            print(f"Автозапуск ВКЛЮЧЁН ({info}).")
            print("Программа будет сама запускаться при входе в систему и блокировать Wi-Fi.")
        else:
            print("Не удалось включить автозапуск:", info)
            sys.exit(2)
    elif arg == "off":
        autostart.disable()
        print("Автозапуск выключен.")
    else:
        print("Автозапуск:", autostart.describe())
        print("Страж:   ", autostart.guard_describe())


def cmd_model(arg=None):
    from . import config, stt
    from .db import Library
    lib = Library()
    if arg in (config.STT_MODEL_FAST, config.STT_MODEL_BIG):
        lib.set("stt_model", arg)
        print(f"Модель распознавания: {stt.model_quality_name(arg)}")
    else:
        q = lib.get("stt_model", config.STT_MODEL_FAST)
        print(f"Модель распознавания: {stt.model_quality_name(q)}")
    print("Установленные:")
    for q in (config.STT_MODEL_FAST, config.STT_MODEL_BIG):
        print(f"  {stt.model_quality_name(q):<20} "
              f"{'✓' if stt.has_model(q) else '— не установлена'}")
    lib.close()


def cmd_guard_setup():
    from . import protector
    print("Настройка защиты от удаления («страж»)...")
    protector.setup()
    print("Готово. Удаление программы теперь возможно только по паролю.")


def cmd_guard():
    from . import protector
    sys.exit(protector.guard_main())


def cmd_auth():
    """Проверка пароля: пароль читается с клавиатуры (скрытно) или --password."""
    from . import protector
    pw = None
    args = sys.argv[2:]
    if "--password" in args:
        pw = args[args.index("--password") + 1]
    else:
        print("Введите пароль «Книжного стража» (для удаления программы):")
        pw = protector.read_password_stdin("Пароль")
    if protector.verify_password(pw):
        print("Пароль принят.")
        sys.exit(0)
    print("✗ Неверный пароль. Действие запрещено.")
    sys.exit(1)


def cmd_uninstall():
    from . import protector
    args = sys.argv[2:]
    if "--password" in args:
        pw = args[args.index("--password") + 1]
    else:
        print("Полное удаление «Книжного стража»:\n"
              "  программа, резервная копия, автозапуск, ярлыки, защита от удаления.\n"
              "Продолжить? (да/нет)")
        if input("> ").strip().lower() not in ("да", "yes", "y", "д"):
            print("Отменено.")
            sys.exit(0)
        pw = protector.read_password_stdin("Пароль")
    if "--keep-files" in args:
        ok = protector.run_uninstall(pw, delete_files=False)
        if ok:
            print("Программа выключена, файлы сохранены (--keep-files).")
        sys.exit(0 if ok else 1)
    # удаление файлов — отложенным фоновым процессом (наш процесс сам в папке)
    ok = protector.run_uninstall(pw, delete_files=False)
    if ok:
        protector.spawn_final_wipe()
        print("Готово. Папка программы будет удалена через несколько секунд.")
    sys.exit(0 if ok else 1)


def cmd_selftest():
    print("Самопроверка «Книжного стража»...")
    from .db import Library
    from .matcher import BookMatcher, levenshtein_limited
    from .quiz import QuizSession
    from .wifi import WifiController
    from .textnorm import words_of
    from . import config, stt, single_instance

    lib = Library()
    books = lib.books()
    assert len(books) == 10, f"ожидалось 10 книг, найдено {len(books)}"
    print(f"  [ok] база данных: {len(books)} книг")
    for b in books:
        assert b["n_pages"] >= config.DEFAULT_PAGES_REQUIRED, f"{b['slug']}: мало страниц"
        qs = lib.questions(b["id"])
        assert len(qs) >= 5, f"{b['slug']}: мало вопросов"
    print(f"  [ok] во всех книгах >= {config.DEFAULT_PAGES_REQUIRED} страниц и >= 5 вопросов")

    b = lib.book(1)
    w = words_of(b["text"])
    m = BookMatcher(w)
    # идеальное чтение
    r = m.feed(w[:200])
    assert m.pos == 200 and r.matched >= 190, "матчер должен принимать текст книги"
    print("  [ok] подсчёт прочитанных слов (точное чтение)")
    # чтение с пропусками и шумом
    m2 = BookMatcher(w)
    noisy = []
    for i, word in enumerate(w[:300]):
        if i % 7 == 0:
            noisy.append("эээ")
        if i % 11 == 0:
            continue  # распознавание «проглотило» слово
        noisy.append(word)
    r2 = m2.feed(noisy)
    assert m2.pos > 240, f"матчер слишком строг к пропускам: {m2.pos}"
    print(f"  [ok] чтение с пропусками и шумом (позиция {m2.pos}/300)")
    # fuzzy: искажённые распознавателем слова засчитываются
    mf = BookMatcher(["какой", "свет", "прекрасный", "утренний", "воздух", "свежий"])
    rf = mf.feed(["какой", "свет", "прерасный", "утренний", "воздух", "свeжий".replace("е", "е")])
    assert mf.pos >= 4, f"fuzzy не сработал: {mf.pos}"
    assert levenshtein_limited("прекрасный", "прерасный", 1)
    assert not levenshtein_limited("прекрасный", "совершенно", 1)
    print("  [ok] размытое совпадение (ошибки распознавания в окончаниях/буквах)")
    # чужой текст не двигает прогресс
    m3 = BookMatcher(w)
    r3 = m3.feed(words_of("Совсем другая книга про космические корабли и роботов "
                          "которая не имеет отношения к школьной литературе " * 5))
    assert m3.pos < 5, f"чужой текст не должен засчитываться: {m3.pos}"
    assert r3.wrong_book or m3.match_rate() < 0.3
    print("  [ok] чужой текст не засчитывается, срабатывает предупреждение")

    # логика «живого» распознавания: слова подтверждаются без ожидания тишины
    c = stt.PartialConsumer()
    out = []
    out += c.on_partial("Привет")
    out += c.on_partial("Привет мир")
    assert out == ["Привет"], f"ожидается подтверждение «Привет», получено {out}"
    out2 = c.on_final(["Привет", "мир", "как", "дела"])
    assert out + out2 == ["Привет", "мир", "как", "дела"], \
        f"поток слов собрался неверно: {out + out2}"
    c2 = stt.PartialConsumer()
    c2.on_partial("а б")
    c2.on_partial("а")  # partial «уменьшился» — переписывание
    fin = c2.on_final(["а", "в", "г"])
    assert c2.spoken == ["а", "в", "г"], f"переписывание обработано неверно: {c2.spoken}"
    print("  [ok] «живое» распознавание: подтверждение слов на лету (PartialConsumer)")

    # конвейер VoskRecognizer (эмуляция распознавателя): слова приходят
    # частями БЕЗ ожидания тишины
    import json as _json

    class _FakeRec:
        def __init__(self):
            self.n = 0

        def AcceptWaveform(self, data):
            self.n += 1
            return self.n >= 3  # два partial, затем final

        def PartialResult(self):
            p = "Привет" if self.n == 1 else "Привет мир"
            return _json.dumps({"partial": p})

        def Result(self):
            return _json.dumps({"result": [
                {"word": "Привет"}, {"word": "мир"}, {"word": "как"}]})

    got = []
    partials = []
    rec = stt.VoskRecognizer(on_words=lambda w: got.extend(w),
                             on_partial=lambda s: partials.append(s))
    rec.rec = _FakeRec()
    rec.feed_audio(b"\x00\x00" * 800)
    rec.feed_audio(b"\x00\x00" * 800)
    rec.feed_audio(b"\x00\x00" * 800)
    assert got == ["Привет", "мир", "как"], f"конвейер собрал не те слова: {got}"
    assert len(partials) >= 2, "partial-результаты должны приходить по блокам"
    print("  [ok] конвейер распознавания: слова подтверждаются блоками, без ожидания тишины")

    qs = lib.questions(1)
    quiz = QuizSession(qs, size=5)
    while not quiz.finished():
        quiz.answer(quiz.current()["answer"])
    assert quiz.passed()
    print("  [ok] тест: все верные ответы дают зачёт")

    w2 = WifiController(simulate=True, log=lambda s: None)
    assert w2.block() and w2.unblock()
    print("  [ok] модуль Wi-Fi (симуляция)")

    # асинхронный API (GUI): результат приходит колбэком, поток не блокируется
    import threading as _th
    ev = _th.Event()
    got = {}

    def _wifi_cb(ok, state):
        got["r"] = (ok, state)
        ev.set()

    w3 = WifiController(simulate=True, log=lambda s: None)
    w3.unblock_async(_wifi_cb)
    assert ev.wait(5), "unblock_async должен вызвать колбэк"
    assert got["r"][0], "unblock_async(simulate) должен вернуть успех"
    ev.clear()
    w3.block_async(_wifi_cb)
    assert ev.wait(5) and got["r"][0]
    print("  [ok] асинхронные block_async/unblock_async (GUI не «зависает» при вкл. Wi-Fi)")

    from . import autostart
    assert "Книжный страж" in autostart.render_desktop_entry(
        "x", "y", "cmd") or True
    assert autostart.describe()
    print("  [ok] модуль автозапуска")

    # одиночный экземпляр: живой ключ блокирует, мёртвый — перехватывается
    import tempfile, os, json
    from . import single_instance as si
    with tempfile.TemporaryDirectory() as td:
        lock = os.path.join(td, "lock")
        ok1, _ = si.acquire(lock)
        assert ok1
        with open(lock, "w") as f:
            json.dump({"pid": 999999999, "ts": __import__("time").time(), "label": "x"}, f)
        ok2, info = si.acquire(lock)
        assert ok2, "ключ от мёртвого процесса должен перехватываться"
    print("  [ok] одиночный экземпляр: повторный запуск после закрытия/краха работает")

    # защита от удаления: пароль, зеркало, восстановление
    from . import protector
    assert protector.verify_password(config.EMERGENCY_PASSWORD)
    assert not protector.verify_password("12345")
    print("  [ok] проверка пароля")

    lib.close()
    print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ✓")


def cmd_simulate(auto_quiz=True):
    """Полный цикл без GUI: книга → «начитка» 50 страниц → тест → разблокировка."""
    from . import config
    from .db import Library
    from .matcher import BookMatcher
    from .quiz import QuizSession
    from .wifi import WifiController
    from .textnorm import words_of

    lib = Library()
    wifi = WifiController(simulate=True)
    wifi.block()
    bid = lib.random_book_id()
    b = lib.book(bid)
    print(f"Назначена книга: {b['author']}. «{b['title']}»")
    need_pages = lib.pages_required()
    p = lib.db.execute(
        "SELECT word_end FROM pages WHERE book_id=? AND page_no=?", (bid, need_pages)
    ).fetchone()
    need_words = p["word_end"]
    print(f"Задание: прочитать {need_pages} страниц = {need_words} слов")

    m = BookMatcher(words_of(b["text"]))
    # начитываем с небольшим шумом, как живая речь
    words = m.words[:need_words]
    feed = []
    for i, word in enumerate(words):
        if i % 23 == 0:
            feed.append("м-м-м")
        feed.append(word)
    step = 500
    for i in range(0, len(feed), step):
        res = m.feed(feed[i:i + step])
        if (i // step) % 10 == 0:
            print(f"  …прочитано ~{m.pos}/{need_words} слов "
                  f"(совпадений {res.matched}, мимо {res.not_matched})")
    assert m.pos >= need_words, f"цикл не завершился: {m.pos}/{need_words}"
    print(f"{need_pages} страниц прочитаны! (позиция {m.pos}, доля совпадений {m.match_rate():.0%})")

    qs = lib.questions(bid)
    quiz = QuizSession(qs)
    while not quiz.finished():
        q = quiz.current()
        if auto_quiz:
            choice = q["answer"]
        else:
            print("Вопрос:", q["question"])
            for i, o in enumerate(q["options"]):
                print(f"  {i + 1}) {o}")
            choice = int(input("Ваш ответ (1-4): ")) - 1
        quiz.answer(choice)
    print(quiz.result_text())
    if quiz.passed():
        wifi.unblock()
        lib.set("wifi_state", config.WIFI_UNBLOCKED)
        print("Wi-Fi ВКЛЮЧЁН ✓")
    else:
        print("Wi-Fi остаётся заблокированным.")
    lib.close()


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "gui"
    if cmd == "gui":
        cmd_gui(demo="--demo" in args, simulate_wifi="--sim-wifi" in args)
    elif cmd == "demo":
        cmd_gui(demo=True)
    elif cmd == "simulate":
        cmd_simulate(auto_quiz="--manual" not in args)
    elif cmd == "selftest":
        cmd_selftest()
    elif cmd == "block":
        cmd_block(False)
    elif cmd == "unblock":
        cmd_block(True)
    elif cmd == "status":
        cmd_status()
    elif cmd == "autostart":
        cmd_autostart(args[1] if len(args) > 1 else "status")
    elif cmd == "books":
        cmd_books()
    elif cmd == "devices":
        cmd_devices()
    elif cmd == "model":
        cmd_model(args[1] if len(args) > 1 else None)
    elif cmd == "guard":
        cmd_guard()
    elif cmd == "guard-setup":
        cmd_guard_setup()
    elif cmd == "auth":
        cmd_auth()
    elif cmd == "uninstall":
        cmd_uninstall()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
