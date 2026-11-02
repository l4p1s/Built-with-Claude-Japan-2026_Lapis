"""確定・終了・表示のルールと、備考の検査。数値はここだけで変える（docs/web-ui-design.md 2.3）。"""

import re
import unicodedata
from datetime import timedelta
from typing import Literal, get_args

# 「何があるか」の種類。none（わからない）と ended（もう終わっていた）は含まない
Kind = Literal["water", "food", "goods", "car_sleeping", "shelter", "other"]
KINDS = get_args(Kind)

QUESTION_RADIUS_M = 500  # 「ここに何がある？」を聞く範囲
MERGE_RADIUS_M = 100  # 報告を既存の候補にまとめる範囲
FADE_AFTER = timedelta(hours=3)  # 最後の確認からこの時間で薄く表示する
HIDE_AFTER = timedelta(hours=6)  # 最後の確認からこの時間で地図から消す
CONFIRM_ANSWERS = {"detected": 1, "report": 2}  # 確認済みになるのに必要な種類の回答の人数
NOTE_MAX_LEN = 100
NOTE_HIDE_FLAGS = 2  # この件数の通報で備考を出さなくする

_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_PHONE = re.compile(r"\d(?:[\s\-‐−ー()]*\d){9,}")  # 区切りを挟んで10桁以上の数字


def clean_note(note: str | None) -> str | None:
    """備考を検査して前後の空白を取る。空なら None。書けない内容なら ValueError。"""
    if note is None or not note.strip():
        return None
    note = note.strip()
    if len(note) > NOTE_MAX_LEN:
        raise ValueError(f"備考は{NOTE_MAX_LEN}文字までです")
    plain = unicodedata.normalize("NFKC", note)  # 全角の数字・記号も半角にして調べる
    if _URL.search(plain):
        raise ValueError("備考に URL は書けません")
    if _PHONE.search(plain):
        raise ValueError("備考に電話番号は書けません")
    return note
