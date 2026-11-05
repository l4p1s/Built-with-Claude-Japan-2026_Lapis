"""テストは対象地域の制限と回数制限を切って動かす（確かめるテストは monkeypatch で入れ直す）"""

import os

os.environ.setdefault("DETECT_EVERY_S", "0")
os.environ["AREA"] = ""
os.environ["RATE_LIMIT"] = "off"
