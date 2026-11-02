import pytest

from app.rules import clean_note


def test_clean_note_accepts():
    assert clean_note(None) is None
    assert clean_note("   ") is None
    assert clean_note(" 12時まで。容器を持参 ") == "12時まで。容器を持参"
    assert clean_note("2026/7/29 12:00〜15:00") == "2026/7/29 12:00〜15:00"
    assert clean_note("あ" * 100) == "あ" * 100


@pytest.mark.parametrize(
    "note",
    [
        "あ" * 101,
        "詳細は https://example.com",
        "www.example.com",
        "0965-33-4111 まで",
        "０９０１２３４５６７８",
        "090 1234 5678",
    ],
)
def test_clean_note_rejects(note):
    with pytest.raises(ValueError):
        clean_note(note)
