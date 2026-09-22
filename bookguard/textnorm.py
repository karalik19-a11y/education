# -*- coding: utf-8 -*-
"""Нормализация текста для сравнения речи с книгой."""
import re

WORD_RE = re.compile(r"[а-яёa-z]+(?:-[а-яёa-z]+)?", re.IGNORECASE)


def normalize_word(w: str) -> str:
    w = (w or "").lower().replace("ё", "е")
    m = WORD_RE.search(w)
    return m.group(0) if m else ""


def words_of(text: str):
    """Список нормализованных слов текста (индекс слова = позиция для matcher)."""
    return [m.group(0).lower().replace("ё", "е")
            for m in WORD_RE.finditer(text or "")]


def spans_of(text: str):
    """(start, end) каждого слова в исходном тексте — для подсветки в GUI.

    Поиск идёт по исходной строке (без предварительного lower()): у некоторых
    символов Unicode нижний регистр меняет длину строки, и тогда индексы слов
    разъезжались бы с подсветкой. Длина списка всегда равна len(words_of()).
    """
    return [(m.start(), m.end()) for m in WORD_RE.finditer(text or "")]
