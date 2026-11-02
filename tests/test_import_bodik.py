from scripts.import_bodik import rows


def feature(name, opened_at, lon=130.6, closed_at=None):
    return {
        "geometry": {"coordinates": [lon, 32.5]},
        "properties": {"municipality_code": "432024", "name": name, "address": "八代市",
                       "opened_at": opened_at, "closed_at": closed_at},
    }  # fmt: skip


def test_opened_row_comes_last_for_same_key():
    data = {
        "features": [
            feature("代陽", "2026-07-28T16:27:00+09:00", lon=130.59),
            feature("代陽", None),
        ]
    }
    out = rows(data)
    assert [r[1] for r in out] == ["432024:代陽", "432024:代陽"]
    assert out[-1][6] == "2026-07-28T16:27:00+09:00"  # 上書きで残るのは開設の行
    assert out[-1][4] == 130.59


def test_later_closing_row_wins_when_both_opened():
    data = {"features": [
        feature("益城", "2026-07-29T10:00:00+09:00", closed_at="2026-08-09T10:00:00+09:00"),
        feature("益城", "2026-07-29T10:00:00+09:00", closed_at="2026-07-31T10:00:00+09:00"),
    ]}  # fmt: skip
    assert rows(data)[-1][7] == "2026-08-09T10:00:00+09:00"
