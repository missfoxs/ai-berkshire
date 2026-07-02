"""极简磁盘缓存: 把取到的数据落盘, 反复测试不再重复拉网.

按 key 存 pickle 文件, 用文件 mtime 做 TTL 过期判断.
DataFrame / int 等均可缓存. 缓存目录默认 data/cache/ashare/.
"""

from __future__ import annotations

import os
import pickle
import re
import time
from typing import Callable


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name))


class DiskCache:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.root, _safe(key) + ".pkl")

    def get_or_compute(self, key: str, ttl: float, fn: Callable, refresh: bool = False):
        path = self._path(key)
        if (not refresh and os.path.exists(path)
                and (time.time() - os.path.getmtime(path)) < ttl):
            try:
                with open(path, "rb") as f:
                    return pickle.load(f)
            except Exception:  # noqa: BLE001 - 缓存损坏则重算
                pass
        val = fn()
        try:
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                pickle.dump(val, f)
            os.replace(tmp, path)  # 原子写, 避免半截文件
        except Exception:  # noqa: BLE001 - 落盘失败不影响返回
            pass
        return val
