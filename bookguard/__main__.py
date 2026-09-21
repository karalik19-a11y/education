#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Командная строка «Книжного стража».

  python -m bookguard            — запуск программы (интерфейс)
  python -m bookguard demo       — демо интерфейса без микрофона и админа
  python -m bookguard simulate   — полный прогон цикла без GUI (проверка логики)
  python -m bookguard selftest   — быстрая самопроверка
  python -m bookguard block|unblock|status — управление Wi-Fi
  python -m bookguard books      — список книг в базе
  python -m bookguard devices    — микрофоны в системе
"""
import sys

from . import config


def cmd_gui(demo=False, simulate_wifi=False):
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("Не найден Tkinter. Установите python3-tk (Linux) или Python с Tk (Windows).")
        sys.exit(1)
    from .app import App
    app = App(demo=demo, simulate_wifi=simulate_wifi)
    app.mainloop()


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


def cmd_selftest():
    print("Самопроверка «Книжного стража»...")
    from .db import Library
    from .matcher import BookMatcher
    from .quiz import QuizSession
    from .wifi import WifiController
    from .textnorm import words_of

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
    # чужой текст не двигает прогресс
    m3 = BookMatcher(w)
    r3 = m3.feed(words_of("Совсем другая книга про космические корабли и роботов "
                          "которая не имеет отношения к школьной литературе " * 5))
    assert m3.pos < 5, f"чужой текст не должен засчитываться: {m3.pos}"
    assert r3.wrong_book or m3.match_rate() < 0.3
    print("  [ok] чужой текст не засчитывается, срабатывает предупреждение")

    qs = lib.questions(1)
    quiz = QuizSession(qs, size=5)
    while not quiz.finished():
        quiz.answer(quiz.current()["answer"])
    assert quiz.passed()
    print("  [ok] тест: все верные ответы дают зачёт")

    w2 = WifiController(simulate=True, log=lambda s: None)
    assert w2.block() and w2.unblock()
    print("  [ok] модуль Wi-Fi (симуляция)")
    lib.close()
    print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ✓")


def cmd_simulate(auto_quiz=True):
    """Полный цикл без GUI: книга → «начитка» 50 страниц → тест → разблокировка."""
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
    print(f"50 страниц прочитаны! (позиция {m.pos}, доля совпадений {m.match_rate():.0%})")

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
    elif cmd == "books":
        cmd_books()
    elif cmd == "devices":
        cmd_devices()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
