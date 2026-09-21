#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка библиотеки «Книжный страж»: тома, страницы, база данных SQLite.

Исходные тексты берутся из .downloads/ (см. tools/fetch_sources.sh).
Результат: data/books/*.txt, data/library.db, data/library_summary.txt
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_RUSLIT = ROOT / ".downloads" / "RusLit"
SRC_EXTRA = ROOT / ".downloads" / "extra"
DATA = ROOT / "data"
BOOKS_DIR = DATA / "books"
DB_PATH = DATA / "library.db"

PAGE_CHARS = 1000          # 1 «страница» ≈ 1000 знаков чистого текста
PAGES_REQUIRED = 50        # сколько страниц нужно прочитать для доступа

# ---------------------------------------------------------------- тома библиотеки
BOOKS = [
    dict(
        slug="pushkin_dubrovsky", num=1,
        author="А. С. Пушкин", title="Дубровский",
        tagline="Повесть",
        parts=[("R", "prose/Pushkin/Дубровский.txt")],
    ),
    dict(
        slug="lermontov_mtsyri", num=2,
        author="М. Ю. Лермонтов", title="Мцыри. Ашик-Кериб. Стихотворения",
        tagline="Поэма, сказка и лирика",
        parts=[
            ("R", "poems/Lermontov/Мцыри.txt"),
            ("R", "prose/Lermontov/Ашик-Кериб.txt"),
            ("R", "poems/Lermontov/Бородино.txt"),
            ("R", "poems/Lermontov/Валерик.txt"),
            ("R", "poems/Lermontov/Смерть поэта.txt"),
            ("R", "poems/Lermontov/Дума.txt"),
            ("R", "poems/Lermontov/Родина.txt"),
            ("R", "poems/Lermontov/Тучи.txt"),
            ("R", "poems/Lermontov/Парус.txt"),
            ("R", "poems/Lermontov/Пророк.txt"),
            ("R", "poems/Lermontov/Ангел.txt"),
            ("R", "poems/Lermontov/Молитва (В минуту жизни трудную...).txt"),
            ("R", "poems/Lermontov/Молитва (Я, матерь божия, ныне с молитвою...).txt"),
            ("R", "poems/Lermontov/Выхожу один я на дорогу.txt"),
            ("R", "poems/Lermontov/И скучно и грустно.txt"),
            ("R", "poems/Lermontov/Аминт твой на глупца походит....txt"),
            ("R", "poems/Lermontov/А. О. Смирновой.txt"),
            ("R", "poems/Lermontov/Ах! сокрылась в мрак ненастный....txt"),
            ("R", "poems/Lermontov/Из-под таинственной холодной полумаски.txt"),
        ],
    ),
    dict(
        slug="gogol_noch", num=3,
        author="Н. В. Гоголь", title="Ночь перед Рождеством",
        tagline="Повесть",
        parts=[("R", "prose/Gogol/Ночь перед Рождеством.txt")],
    ),
    dict(
        slug="turgenev_zapiski", num=4,
        author="И. С. Тургенев", title="Записки охотника",
        tagline="Рассказы: «Бежин луг» и другие",
        parts=[("R", "prose/Turgenev/Записки охотника.txt")],
        slice_marks=("Бежин луг", "Примечания"),
    ),
    dict(
        slug="nekrasov_stihi", num=5,
        author="Н. А. Некрасов", title="Крестьянские дети и стихотворения",
        tagline="Поэма и лирика",
        parts=[
            ("R", "poems/Nekrasov/Крестьянские дети.txt"),
            ("R", "poems/Nekrasov/Мороз, красный нос.txt"),
            ("R", "poems/Nekrasov/Железная дорога.txt"),
            ("R", "poems/Nekrasov/Размышления у парадного подъезда.txt"),
            ("R", "poems/Nekrasov/Тройка.txt"),
            ("R", "poems/Nekrasov/Поэт и гражданин.txt"),
            ("R", "poems/Nekrasov/Элегия.txt"),
            ("R", "poems/Nekrasov/Дедушка.txt"),
            ("R", "poems/Nekrasov/В дороге.txt"),
            ("R", "poems/Nekrasov/Вчерашний день, часу в шестом....txt"),
            ("R", "poems/Nekrasov/Мы с тобой бестолковые люди....txt"),
            ("R", "poems/Nekrasov/О Муза! я у двери гроба....txt"),
            ("R", "poems/Nekrasov/Я не люблю иронии твоей....txt"),
        ],
    ),
    dict(
        slug="tolstoy_kavkaz", num=6,
        author="Л. Н. Толстой", title="Кавказский пленник",
        tagline="Рассказы о Кавказе",
        parts=[
            ("R", "prose/Tolstoy/Кавказский пленник.txt"),
            ("R", "prose/Tolstoy/Набег.txt"),
            ("R", "prose/Tolstoy/Рубка леса.txt"),
            ("R", "prose/Tolstoy/Разжалованный.txt"),
        ],
    ),
    dict(
        slug="leskov_levsha", num=7,
        author="Н. С. Лесков", title="Левша",
        tagline="Сказ",
        parts=[
            ("E", "Левша.txt"),
            ("E", "Тупейный художник.txt"),
            ("E", "Человек на часах.txt"),
        ],
    ),
    dict(
        slug="chekhov_rasskazy", num=8,
        author="А. П. Чехов", title="Рассказы",
        tagline="«Хамелеон» и другие рассказы",
        parts=[
            ("R", "prose/Chekhov/Хамелеон.txt"),
            ("R", "prose/Chekhov/Толстый и тонкий.txt"),
            ("R", "prose/Chekhov/Смерть чиновника.txt"),
            ("R", "prose/Chekhov/Хирургия.txt"),
            ("R", "prose/Chekhov/Лошадиная фамилия.txt"),
            ("R", "prose/Chekhov/Злоумышленник.txt"),
            ("R", "prose/Chekhov/Ванька.txt"),
            ("R", "prose/Chekhov/Тоска.txt"),
            ("R", "prose/Chekhov/Душечка.txt"),
            ("R", "prose/Chekhov/Каштанка.txt"),
            ("R", "prose/Chekhov/Пересолил.txt"),
            ("R", "prose/Chekhov/Дочь Альбиона.txt"),
            ("R", "prose/Chekhov/Беглец.txt"),
            ("R", "prose/Chekhov/Белолобый.txt"),
        ],
    ),
    dict(
        slug="prishvin_kladovaya", num=9,
        author="М. М. Пришвин", title="Кладовая солнца",
        tagline="Повесть",
        parts=[("E", "Кладовая солнца.txt")],
    ),
    dict(
        slug="pushkin_belkin", num=10,
        author="А. С. Пушкин", title="Повести Белкина",
        tagline="«Выстрел», «Метель» и другие повести",
        parts=[("R", "prose/Pushkin/Повести Белкина.txt")],
    ),
]

# ---------------------------------------------------------------- уборка текста
FOOTNOTE_RE = re.compile(r"(?<=[а-яёa-zА-ЯЁA-Z\)])\s[1-9](?=[\s.,;:!?…—\)\"]|$)")
DASH_FIX = re.compile(r"[ \t]*—[ \t]*")


def clean_text(raw: str) -> str:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "").replace("\xa0", " ")
    raw = raw.replace("\t", " ")
    # сноски-цифры («слово 1,» -> «слово,»)
    raw = FOOTNOTE_RE.sub("", raw)
    lines = [re.sub(r"[ ]{2,}", " ", ln).strip() for ln in raw.split("\n")]
    out, blank = [], 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(ln)
    text = "\n".join(out).strip()
    return text


def read_part(src: str, rel: str) -> str:
    base = SRC_RUSLIT if src == "R" else SRC_EXTRA
    p = base / rel
    if not p.exists():
        sys.exit(f"НЕТ ИСХОДНИКА: {p}\nСначала выполните tools/fetch_sources.sh")
    return clean_text(p.read_text(encoding="utf-8", errors="replace"))


def slice_between(text: str, marks) -> str:
    start_m, end_m = marks
    i = text.find(start_m)
    if i < 0:
        sys.exit(f"не найдена метка начала: {start_m!r}")
    j = text.find(end_m, i + len(start_m))
    return text[i:j if j > 0 else len(text)]


def strip_notes_tail(text: str) -> str:
    """Отрезать хвостовые примечания/сноски сборника."""
    for mark in ("\nПримечания", "\nЗамечание пасичника"):
        j = text.find(mark)
        if j > 0:
            text = text[:j]
    return text.strip()


WORD_RE = re.compile(r"[а-яёa-z]+(?:-[а-яёa-z]+)?")


def words_of(text: str):
    return [w.lower().replace("ё", "е") for w in WORD_RE.findall(text.lower())]


def make_pages(text: str):
    """Нарезка текста на страницы по ~PAGE_CHARS знаков по границам абзацев/слов."""
    paras = [p for p in text.split("\n") if p.strip()]
    pages = []  # (char_start, char_end)
    cur_start, cur_len = 0, 0
    pos = 0
    for p in paras:
        plen = len(p) + 1
        if cur_len + plen > PAGE_CHARS and cur_len > 0:
            pages.append((cur_start, pos - 1))
            cur_start, cur_len = pos, 0
        # очень длинный абзац режем по словам
        while cur_len + plen > PAGE_CHARS * 2:
            cut = p[: PAGE_CHARS * 2].rfind(" ")
            if cut < 10:
                cut = PAGE_CHARS
            piece = p[:cut]
            pages.append((cur_start, cur_start + len(piece)))
            p = p[cut:].lstrip()
            pos = cur_start = cur_start + len(piece)
            cur_len, plen = 0, len(p) + 1
        cur_len += plen
        pos += plen
    if cur_len > 0:
        pages.append((cur_start, len(text)))
    # границы слов: для каждой страницы индексы слов
    word_spans = [(m.start(), m.end()) for m in WORD_RE.finditer(text.lower())]
    page_words = []
    wi = 0
    for (cs, ce) in pages:
        start_i = wi
        while wi < len(word_spans) and word_spans[wi][0] < ce:
            wi += 1
        page_words.append((start_i, wi))
    return pages, page_words, len(word_spans)


def main():
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    quiz_path = DATA / "quiz.json"
    quiz = json.loads(quiz_path.read_text(encoding="utf-8"))

    if DB_PATH.exists():
        DB_PATH.unlink()
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE books(
          id INTEGER PRIMARY KEY, slug TEXT UNIQUE, author TEXT, title TEXT, tagline TEXT,
          text TEXT, n_pages INTEGER, n_words INTEGER, n_chars INTEGER);
        CREATE TABLE pages(
          id INTEGER PRIMARY KEY, book_id INTEGER, page_no INTEGER,
          char_start INTEGER, char_end INTEGER, word_start INTEGER, word_end INTEGER);
        CREATE TABLE questions(
          id INTEGER PRIMARY KEY, book_id INTEGER, question TEXT,
          opt1 TEXT, opt2 TEXT, opt3 TEXT, opt4 TEXT, answer INTEGER, about TEXT);
        CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT);
        """
    )

    summary = []
    for b in BOOKS:
        chunks = []
        marks = b.get("slice_marks")
        for src, rel in b["parts"]:
            t = read_part(src, rel)
            if marks and rel == b["parts"][0][1]:
                t = slice_between(t, marks)
            t = strip_notes_tail(t)
            chunks.append((rel, t))
        # заголовок тома
        head = f"{b['author']}. {b['title']}\n"
        body_parts = []
        offset_map = []  # (rel, start, end) внутри итогового текста
        cur = len(head) + 1
        for rel, t in chunks:
            title_line = Path(rel).stem
            piece = f"{title_line}\n\n{t}"
            offset_map.append((title_line, cur, cur + len(piece)))
            body_parts.append(piece)
            cur += len(piece) + 2
        text = head + "\n" + "\n\n".join(body_parts) + "\n"
        text = re.sub(r"\n{3,}", "\n\n", text)

        pages, page_words, n_words = make_pages(text)
        n_chars = len(text)
        out_path = BOOKS_DIR / f"{b['num']:02d}_{b['slug']}.txt"
        out_path.write_text(text, encoding="utf-8")

        cur = db.execute(
            "INSERT INTO books(slug, author, title, tagline, text, n_pages, n_words, n_chars)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (b["slug"], b["author"], b["title"], b["tagline"], text, len(pages), n_words, n_chars),
        ).lastrowid
        for i, ((cs, ce), (ws, we)) in enumerate(zip(pages, page_words), 1):
            db.execute(
                "INSERT INTO pages(book_id, page_no, char_start, char_end, word_start, word_end)"
                " VALUES(?,?,?,?,?,?)", (cur, i, cs, ce, ws, we))

        qs = quiz.get(b["slug"], [])
        if len(qs) < 5:
            sys.exit(f"для {b['slug']} нужно >=5 вопросов, найдено {len(qs)}")
        for q in qs:
            if len(q["options"]) != 4 or not (0 <= q["answer"] <= 3):
                sys.exit(f"плохой вопрос в {b['slug']}: {q}")
            db.execute(
                "INSERT INTO questions(book_id, question, opt1, opt2, opt3, opt4, answer, about)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (cur, q["q"], *q["options"], q["answer"], q.get("about", "")),
            )

        first50 = min(50, len(pages))
        w50 = page_words[first50 - 1][1] if first50 else 0
        summary.append(
            f"{b['num']:>2}. {b['author']}. {b['title']} — стр.: {len(pages)}, "
            f"знаков: {n_chars}, слов: {n_words}, вопросов: {len(qs)}, "
            f"словоc запас 50 стр.: {w50}"
        )
        if len(pages) < PAGES_REQUIRED:
            summary[-1] += "  [!] МАЛО СТРАНИЦ"
        if n_chars < PAGE_CHARS * (PAGES_REQUIRED + 5):
            summary[-1] += "  [!] малый запас текста"

    for k, v in (("pages_required", str(PAGES_REQUIRED)),
                 ("page_chars", str(PAGE_CHARS)),
                 ("pass_ratio", "0.6"),
                 ("quiz_size", "5")):
        db.execute("INSERT INTO state(key, value) VALUES(?,?)", (k, v))
    db.commit()
    db.close()

    txt = "Библиотека «Книжный страж» — 10 книг школьной программы (7 класс)\n" + "\n".join(summary)
    (DATA / "library_summary.txt").write_text(txt + "\n", encoding="utf-8")
    print(txt)
    print(f"\nБаза данных: {DB_PATH} ({DB_PATH.stat().st_size} байт)")


if __name__ == "__main__":
    main()
