#!/usr/bin/env python3
"""公司行動前瞻行事曆:除權息 + 股票分割(面額變更)/減資 → corp_actions.json

四個來源全部**免金鑰**,所以這支不需要 FINMIND_TOKEN:

  除權息(前瞻)  TWSE  rwd/zh/exRight/TWT48U            未來約兩個月
                TPEx  openapi/v1/tpex_exright_prepost
  分割/減資      TWSE  openapi/v1/opendata/t187ap04_L   每日重大訊息
                TPEx  openapi/v1/mopsfin_t187ap04_O

為什麼不用 FinMind:見 corp_actions.py 檔頭(三個相關 dataset 的未來日期實測都是 0 筆)。

**這支是累積式的。** 重大訊息是「每日」feed,只有當下公告期間內的;
每次跑都跟既有 corp_actions.json 合併去重。公告會在 feed 裡留三個月
(寶雅主旨寫「公告期間:115年7月2日至115年10月1日」),所以漏抓幾天補得回來——
這是排程容錯的來源,不是可以不跑的理由。

因為公告不挑交易日發,這支要**每天跑,含週末**。

用法:
    python3 build_corporate_actions.py              # 抓取、合併、寫檔
    python3 build_corporate_actions.py --dry-run    # 只印摘要,不寫檔
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corp_actions import (  # noqa: E402
    classify, days_ahead, event_key, fill_halt, halt_weekdays,
    parse_split_schedule, roc_to_iso,
)

ROOT = Path(__file__).resolve().parent
TPE = dt.timezone(dt.timedelta(hours=8))
OUT = ROOT / "corp_actions.json"
KEEP_PAST_DAYS = 30          # 事件過期多久後從檔案裡移除
UA = {"User-Agent": "Mozilla/5.0 (compatible; twradar-corp-actions/1.0)"}

SRC = {
    "exdiv_twse": "https://www.twse.com.tw/rwd/zh/exRight/TWT48U?response=json",
    "exdiv_tpex": "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost",
    "mops_twse": "https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
    "mops_tpex": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O",
}


def fetch(url: str, timeout: int = 60):
    """單一來源失敗不該讓整份行事曆消失——回 None,呼叫端保留既有資料。"""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:                          # noqa: BLE001
        print(f"  ! 取不到 {url}: {e}", file=sys.stderr)
        return None


def num(x) -> float | None:
    try:
        v = float(str(x).replace(",", "").strip())
        return v if v else None
    except (TypeError, ValueError):
        return None


# ── 除權息 ────────────────────────────────────────────────────────────────
def exdiv_twse(today: dt.date) -> list[dict]:
    j = fetch(SRC["exdiv_twse"])
    out = []
    for r in (j or {}).get("data") or []:
        d = roc_to_iso(r[0])
        if not d:
            continue
        out.append({"date": d, "code": r[1], "name": r[2].strip(), "market": "上市",
                    "kind": r[3], "cash": num(r[7]), "stock_ratio": num(r[4]),
                    "days_ahead": days_ahead(d, today)})
    return out


def exdiv_tpex(today: dt.date) -> list[dict]:
    j = fetch(SRC["exdiv_tpex"])
    out = []
    for r in j or []:
        d = roc_to_iso(r.get("ExRrightsExDividendDate", ""))
        if not d:
            continue
        out.append({"date": d, "code": r.get("SecuritiesCompanyCode"),
                    "name": (r.get("CompanyName") or "").strip(), "market": "上櫃",
                    "kind": r.get("ExRrightsExDividend"), "cash": num(r.get("CashDividend")),
                    "stock_ratio": num(r.get("StockDividendRatio")),
                    "days_ahead": days_ahead(d, today)})
    return out


# ── 分割 / 減資（每日重大訊息）─────────────────────────────────────────────
def mops_rows(today: dt.date) -> list[dict]:
    """兩個市場的欄位名不同(上市中文、上櫃英文),在這裡就抹平。"""
    feeds = (
        (fetch(SRC["mops_twse"]) or [], "上市",
         {"code": "公司代號", "name": "公司名稱", "subj": "主旨 ", "clause": "符合條款",
          "fact": "事實發生日", "note": "說明", "sdate": "發言日期"}),
        (fetch(SRC["mops_tpex"]) or [], "上櫃",
         {"code": "SecuritiesCompanyCode", "name": "CompanyName", "subj": "主旨",
          "clause": "符合條款", "fact": "事實發生日", "note": "說明", "sdate": "發言日期"}),
    )
    out = []
    for rows, market, k in feeds:
        for r in rows:
            subj = str(r.get(k["subj"]) or r.get(k["subj"].strip()) or "").replace("\r", " ")
            kind = classify(subj, str(r.get(k["clause"]) or ""))
            if not kind:
                continue
            note = str(r.get(k["note"]) or "").replace("\r", "")
            fact = roc_to_iso(r.get(k["fact"]) or "") or ""
            # 減資也套同一支解析器:標籤對得上就抓得到,對不上留 None。
            # (減資的時程用「減資基準日」「停止買賣期間」等不同標籤,目前多半解不出來
            #  ——列為已知限制,不是靜默失敗:欄位是 None,前端要顯示「公告未載」。)
            sch = parse_split_schedule(note)
            e = {"kind": kind, "code": str(r.get(k["code"]) or "").strip(),
                 "name": (r.get(k["name"]) or "").strip(), "market": market,
                 "subject": " ".join(subj.split())[:120],
                 "clause": str(r.get(k["clause"]) or ""),
                 "fact_date": fact, "announce_date": roc_to_iso(r.get(k["sdate"]) or "") or "",
                 **sch}
            e = fill_halt(e)
            e["halt_weekdays"] = halt_weekdays(e)
            e["days_ahead"] = days_ahead(e.get("resume"), today)
            e["_key"] = event_key(e["code"], fact, subj)
            out.append(e)
    return out


def merge(old: list[dict], new: list[dict], today: dt.date) -> list[dict]:
    """以 _key 去重。同一鍵取「解析到最多欄位」的那筆——世紀 5314 同一件事重貼 19 則,
    早期幾則的說明欄可能還沒有完整時程。

    平手時**取新的**(下面用 >= 而不是 >)。這條踩過:改好標籤變體後,寶得利 5301 的
    停牌區間應該從「推算」升級成「公告」,但新舊記錄填滿的欄位數一樣多,用 > 會讓
    舊值留下來——解析器改好了、檔案卻永遠不更新。來源 feed 才是事實,
    只有在舊記錄「嚴格更完整」時才保留它(例如帶時程表的那則已掉出 feed)。"""
    def filled(e):
        return sum(1 for f in ("last_trade", "halt_start", "halt_end", "resume",
                               "par_after", "price_divisor") if e.get(f))
    by = {}
    for e in old + new:
        k = e.get("_key") or event_key(e.get("code", ""), e.get("fact_date", ""), e.get("subject", ""))
        e["_key"] = k
        if k not in by or filled(e) >= filled(by[k]):
            by[k] = e
    keep = []
    for e in by.values():
        anchor = e.get("resume") or e.get("halt_end") or e.get("fact_date")
        if anchor and days_ahead(anchor, today) is not None \
                and days_ahead(anchor, today) < -KEEP_PAST_DAYS:
            continue
        e["days_ahead"] = days_ahead(e.get("resume") or e.get("halt_start"), today)
        keep.append(e)
    return sorted(keep, key=lambda e: (e.get("resume") or e.get("fact_date") or "9999", e["code"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    now = dt.datetime.now(TPE)
    today = now.date()
    prev = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}

    ex = [e for e in exdiv_twse(today) + exdiv_tpex(today) if (e["days_ahead"] or -99) >= 0]
    ex.sort(key=lambda e: (e["date"], e["code"]))
    acts = merge(prev.get("actions") or [], mops_rows(today), today)

    upcoming = [e for e in acts if (e.get("days_ahead") or -99) >= 0]
    print(f"  除權息 {len(ex)} 筆（今日起）"
          f" / 分割減資 {len(acts)} 筆（其中未發生 {len(upcoming)} 筆）")
    for e in upcoming[:8]:
        print(f"    {e['code']} {e['name']:<10}{e['kind']:<6} 停牌 {e.get('halt_start')}~"
              f"{e.get('halt_end')} [{e.get('halt_source')}/{e.get('halt_weekdays')}個平日]"
              f" 復牌 {e.get('resume')} 還有 {e.get('days_ahead')} 天")

    if a.dry_run:
        return 0
    OUT.write_text(json.dumps({
        "schema": 1, "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "date": today.isoformat(), "exdiv": ex, "actions": acts,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"  ✓ 寫入 {OUT.name}（除權息 {len(ex)} / 公司行動 {len(acts)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
