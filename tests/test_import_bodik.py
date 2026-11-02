from scripts.import_bodik import rows


def feature(name, opened_at, lon=130.6):
    return {
        "geometry": {"coordinates": [lon, 32.5]},
        "properties": {"municipality_code": "432024", "name": name, "address": "八代市",
                       "opened_at": opened_at, "closed_at": None},
    }  # fmt: skip


def test_opened_row_comes_last_for_same_key():
    data = {"features": [feature("代陽", "2026-07-28T16:27:00+09:00", lon=130.59), feature("代陽", None)]}
    out = rows(data)
    assert [r[1] for r in out] == ["432024:代陽", "432024:代陽"]
    assert out[-1][6] == "2026-07-28T16:27:00+09:00"  # 上書きで残るのは開設の行
    assert out[-1][4] == 130.59
