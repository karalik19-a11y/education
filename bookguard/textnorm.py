# -*- coding: utf-8 -*-
"""Нормализация текста для сравнения речи с книгой."""
import re

WORD_RE = re.compile(r"[а-яёa-z]+(?:-[а-яёa-z]+)?")


def normalize_word(w: str) -> str:
    w = w.lower().replace("ё", "е")
    m = WORD_RE.search(w)
    return m.group(0) if m else ""


def words_of(text: str):
    """Список нормализованных слов текста (индекс слова = позиция для matcher)."""
    return [normalize_word(w) for w in WORD_RE.findall(text.lower())]


def spans_of(text: str):
    """(start, end) каждого слова в исходном тексте — для подсветки в GUI."""
    return [(m.start(), m.end()) for m in WORD_RE.finditer(text.lower())]
