"""書き込みと地図の読み込みの回数制限。プロセス内の記録だけで判定する（ワーカーは 1 つの前提）。

RATE_LIMIT=off で止める（テスト・開発）。数値を変えるときはここだけ。
"""

import os
import threading
import time
from collections import deque

from fastapi import HTTPException, status


class Limiter:
    """直近 per_s 秒に limit 回まで（キーごと）"""

    def __init__(self, limit: int, per_s: int):
        self.limit, self.per_s = limit, per_s
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = now or time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.per_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            # ponytail: 古いキーは残り続ける。利用者が増えたら、たまに掃除するか Redis に替える
            return True


LIMITS = {
    "sessions": Limiter(20, 3600),  # 参加の開始: 同じ IP から 1 時間に 20 回
    "locations": Limiter(10, 60),  # 位置の送信: 1 セッション 1 分に 10 回（画面は 60 秒に 1 回）
    "writes": Limiter(60, 3600),  # 回答・報告・通報: 1 セッション 1 時間に 60 回
    "reads": Limiter(60, 60),  # 地図の読み込み: 1 セッション 1 分に 60 回（画面は地図を動かすたびと 60 秒ごと）
}


def check(name: str, key: str) -> None:
    if os.environ.get("RATE_LIMIT") == "off":
        return
    if not LIMITS[name].allow(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "回数が多すぎます。しばらく待ってからやり直してください",
        )
