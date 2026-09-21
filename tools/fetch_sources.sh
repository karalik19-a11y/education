#!/usr/bin/env bash
# Откуда взяты тексты (для повторной сборки базы).
# ВНИМАНИЕ: все произведения — общественное достояние (русская классика).
set -e
cd "$(dirname "$0")/.."
mkdir -p .downloads/extra

echo "1/2 Классика (RusLit)..."
if [ ! -d .downloads/RusLit ]; then
  git clone --depth 1 https://github.com/d0rj/RusLit.git .downloads/RusLit
fi

echo "2/2 Лесков и Пришвин (библиотека library_tg)..."
gh api repos/FaraamFide/library_tg/contents/books/leskov/Левша.txt -H "Accept: application/vnd.github.raw" > .downloads/extra/Левша.txt
gh api repos/FaraamFide/library_tg/contents/books/leskov/Тупейный%20художник.txt -H "Accept: application/vnd.github.raw" > ".downloads/extra/Тупейный художник.txt"
gh api repos/FaraamFide/library_tg/contents/books/leskov/Человек%20на%20часах.txt -H "Accept: application/vnd.github.raw" > ".downloads/extra/Человек на часах.txt"
gh api repos/FaraamFide/library_tg/contents/books/prishvin/Кладовая%20солнца.txt -H "Accept: application/vnd.github.raw" > ".downloads/extra/Кладовая солнца.txt"

echo "Сборка базы..."
python3 tools/build_library.py
