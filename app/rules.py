"""確定・終了・表示のルールと、備考の検査。数値はここだけで変える（docs/web-ui-design.md 2.3）。"""

import math
import os
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
MAX_SPOTS = 500  # 1 回の地図の読み込みで返す非公式の拠点の上限（新しく確認された順）


def area() -> tuple[float, float, float] | None:
    """対象地域（災害地域）。環境変数 AREA="lat,lon,radius_m"（例 八代市役所から 25km）。空なら制限しない"""
    v = os.environ.get("AREA", "").strip()
    if not v:
        return None
    lat, lon, r = (float(x) for x in v.split(","))
    return lat, lon, r


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """2 点の距離（m）。数十 km までの近似"""
    lat_m = 110574.0
    lon_m = 111320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat1 - lat2) * lat_m, (lon1 - lon2) * lon_m)


def in_area(lat: float, lon: float) -> bool:
    a = area()
    return a is None or distance_m(lat, lon, a[0], a[1]) <= a[2]


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
