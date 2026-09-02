#!/usr/bin/env python3
# tests/test_sites_consistency.py
# 換址點一致性:sites.py(Python)與 index.html 的 SITE(前端)必須指向同一個站台,
# 且「← 回 Hub」那個純 HTML 連結(插不了值,只能另寫一份)也要跟著。
#
# 搬家時漏改一處不會有任何錯誤訊息 —— 前端只是拿到 404 或空白,伺服器端一切正常。
# 這支把「忘記同步」變成 CI 會擋下來的失敗。
#
# 執行:python tests/test_sites_consistency.py
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sites import HUB, RAW_ORG, WORKER  # noqa: E402

HTML = (ROOT / "index.html").read_text(encoding="utf-8")
fails: list[str] = []


def check(ok: bool, msg: str) -> None:
    if ok:
        print(f"  ✓ {msg}")
    else:
        print(f"  ✗ {msg}")
        fails.append(msg)


def site() -> dict:
    m = re.search(r"const SITE = \{(.*?)\};", HTML, re.S)
    assert m, "index.html 找不到 SITE 物件"
    out = {}
    for key in ("rawOrg", "worker", "hub"):
        km = re.search(rf'{key}:\s*"([^"]+)"', m.group(1))
        assert km, f"SITE 缺少 {key}"
        out[key] = km.group(1)
    return out


def main() -> None:
    S = site()
    print("[1] Python 側與前端同源")
    check(RAW_ORG == S["rawOrg"], f"RAW_ORG == SITE.rawOrg ({RAW_ORG})")
    check(WORKER == S["worker"], f"WORKER == SITE.worker ({WORKER})")
    check(HUB == S["hub"], f"HUB == SITE.hub ({HUB})")

    print("[2] 插不了值的靜態 Hub 連結跟著 SITE")
    m = re.search(r'<a class="back" href="([^"]+)">', HTML)
    check(bool(m), "找得到「回 Hub」連結")
    if m:
        check(m.group(1).rstrip("/") == S["hub"].rstrip("/"),
              f'Hub 連結 {m.group(1)} == SITE.hub')

    print("[3] 防回歸:JS 裡不得再出現寫死的位址")
    bad = []
    for i, line in enumerate(HTML.splitlines(), 1):
        t = line.strip()
        if t.startswith(("//", "<!--")) or 'class="back"' in t:
            continue
        if any(k in t for k in ("rawOrg:", "worker:", "hub:")):
            continue
        if re.search(r'"https://(raw\.githubusercontent\.com|[a-z0-9-]+\.github\.io|[a-z0-9-]+\.workers\.dev)', t):
            bad.append(f"{i}: {t[:90]}")
    check(not bad, "沒有寫死的位址" + ("\n      " + "\n      ".join(bad) if bad else ""))

    print("[4] github.com / api.github.com 的帳號與 SITE 一致")
    owner = S["rawOrg"].rstrip("/").split("/")[-1]
    bad2 = []
    for i, line in enumerate(HTML.splitlines(), 1):
        t = line.strip()
        if t.startswith(("//", "<!--")) or "${" in t:
            continue
        for m in re.finditer(r"https://(?:api\.)?github\.com/(?:repos/)?([A-Za-z0-9_.-]+)", t):
            if m.group(1) != owner:
                bad2.append(f"{i}: {t[:80]}")
    check(not bad2, "github 位址帳號一致" + ("\n      " + "\n      ".join(bad2) if bad2 else ""))

    if fails:
        print(f"\n✗ 換址點不一致（{len(fails)} 項）")
        sys.exit(1)
    print("\n✓ 換址點一致")


if __name__ == "__main__":
    main()
