#!/usr/bin/env python3
"""Расшифровка голосового в текст (faster-whisper, русский).

Использование:
    python3 scripts/transcribe.py <файл> [--model small] [--hint "Сбер, Озон, Вика"]

Форматы — всё, что читает ffmpeg (ogg/oga из Telegram, m4a, mp3, wav, aiff).
Модель по умолчанию small/int8: короткое голосовое за секунды на CPU.
--hint — подсказка модели (бренды, имена, термины): без неё «Озон» слышится
как «Азон». Скилл /b2b передаёт сюда бренды из crm.b2b_brands и имена аккаунтов.
Используется скиллом /b2b в Claude и ботом Джангиром на сервере.
"""
import argparse
import os
import sys


def transcribe(path: str, model: str = "small", hint: str | None = None) -> str:
    from faster_whisper import WhisperModel
    m = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _ = m.transcribe(path, language="ru", vad_filter=True,
                               initial_prompt=hint or None)
    return " ".join(s.text.strip() for s in segments).strip()


def main():
    p = argparse.ArgumentParser(description="Расшифровка голосового в текст")
    p.add_argument("path")
    p.add_argument("--model", default="small")
    p.add_argument("--hint", default=None)
    a = p.parse_args()
    if not os.path.isfile(a.path):
        print(f"Файл не найден: {a.path}", file=sys.stderr)
        return 1
    print(transcribe(a.path, a.model, a.hint))
    return 0


if __name__ == "__main__":
    sys.exit(main())
