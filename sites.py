"""單一換址點(Python 側)。

換帳號或換託管只改這裡;CI 上也可以用環境變數覆寫,不必改碼:

    RAW_ORG=https://raw.githubusercontent.com/<你的帳號>
    WORKER_BASE=https://taiwan-flow-v2.<你的帳號>.workers.dev

本 repo 有兩個換址點,搬家時要一起動:
  1. sites.py     這裡(Python 管線)
  2. index.html   SITE 物件(前端 JS);「← 回 Hub」是純 HTML 插不了值,另寫一份

三者由 tests/test_sites_consistency.py 強制一致。

本 repo 的 .py 都在根目錄,`from sites import ...` 不需要路徑處理。
"""
from __future__ import annotations

import os

RAW_ORG = os.environ.get("RAW_ORG", "https://raw.githubusercontent.com/shihpc")
WORKER = os.environ.get("WORKER_BASE", "https://taiwan-flow-v2.shihpc.workers.dev")
HUB = os.environ.get("HUB_BASE", "https://shihpc.github.io")


def raw_base(repo: str) -> str:
    """<RAW_ORG>/<repo>/main"""
    return f"{RAW_ORG}/{repo}/main"


def raw(repo: str, path: str) -> str:
    """<RAW_ORG>/<repo>/main/<path>"""
    return f"{raw_base(repo)}/{path}"
