import os, subprocess, tempfile, pytest
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("transcribe", os.path.join(HERE, "transcribe.py"))
transcribe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transcribe)


@pytest.mark.skipif(subprocess.call(["which", "say"], stdout=subprocess.DEVNULL) != 0, reason="нужен macOS say")
def test_transcribes_russian_speech():
    with tempfile.TemporaryDirectory() as d:
        aiff = os.path.join(d, "v.aiff")
        subprocess.run(["say", "-v", "Milena", "-o", aiff,
                        "Привет, это Вика. Созвонилась со Сбером, перезвонить в четверг."], check=True)
        text = transcribe.transcribe(aiff).lower()
    assert "вика" in text
    assert "сбер" in text
    assert "четверг" in text


def test_missing_file_exits_1():
    r = subprocess.run(["python3", os.path.join(HERE, "transcribe.py"), "/nope.ogg"],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "не найден" in r.stderr


@pytest.mark.skipif(subprocess.call(["which", "say"], stdout=subprocess.DEVNULL) != 0, reason="нужен macOS say")
def test_hint_fixes_brand_spelling():
    with tempfile.TemporaryDirectory() as d:
        aiff = os.path.join(d, "v.aiff")
        subprocess.run(["say", "-v", "Milena", "-o", aiff,
                        "Это Катя. Озон просит перенести звонок на пятницу, бюджет в декабре."], check=True)
        text = transcribe.transcribe(aiff, hint="B2B-клиенты: Сбер, Озон, Яндекс. Аккаунты: Вика, Саша, Катя.")
    assert "Озон" in text
