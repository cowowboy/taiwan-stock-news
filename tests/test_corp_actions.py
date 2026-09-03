#!/usr/bin/env python3
# tests/test_corp_actions.py
# 公司行動公告的解析回歸測試。不連外網:用 tests/fixtures/ 的四份**真實**公告。
#
# 為什麼是這四份:它們是四種不同的寫法,任何一種漏掉都是靜默失敗
# (行事曆少一檔不會有錯誤訊息,只是使用者沒被提醒)。
#   5314 世紀(上櫃)  中文序號 (一)(二),標題只有「面額變更相關事宜」——沒有數字沒有期間
#   5904 寶雅(上櫃)  阿拉伯序號 (1)(2),標題含面額與公告期間
#   6919 康霈(上市)  欄位編號「4.換發股票時程(1)…」,日期帶「民國」前綴
#   6949 沛爾(上市)  持續公告**不含時程表**,最後交易日藏在散文括號裡,且標籤被硬換行切開
#   5301 寶得利(上櫃) 減資,標籤全是變體(「舊股票停止在市場買賣」「減資換發基準日」),
#                     而且計畫「尚未經主管機關核備」——日期還可能變,一定要標出來
#
# 每筆的答案都用 FinMind 日 K 對過:停止交易期間那幾天真的一列資料都沒有。
#
# 執行:python tests/test_corp_actions.py
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import corp_actions as ca  # noqa: E402
from build_corporate_actions import merge  # noqa: E402 — 只用純函式,import 不連外網

FIX = Path(__file__).resolve().parent / "fixtures"
fails: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'✓' if cond else '✗'} {label}")
    if not cond:
        fails.append(label)


def note(code: str) -> str:
    for suffix in ("split", "reduce"):
        p = FIX / f"mops_{code}_{suffix}.txt"
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(code)


# 公告值 → (最後交易, 停牌起, 停牌迄, 基準日, 復牌, 面額前, 面額後, 參考價除數)
# None 代表**公告本身沒寫**,不是解析失敗。
CASES = {
    "5314": ("2025-03-19", "2025-03-20", "2025-03-28", "2025-03-28", "2025-03-31", 10.0, 0.5, 20),
    "5904": ("2026-07-29", "2026-07-30", "2026-08-07", "2026-08-07", "2026-08-10", 10.0, 1.0, 10),
    "6919": ("2025-07-11", "2025-07-14", "2025-07-20", "2025-07-20", "2025-07-21", 5.0, 0.5, 10),
    "6949": ("2026-08-26", None, None, None, "2026-09-07", 10.0, 0.5, 20),
    "5301": ("2026-09-29", "2026-09-30", "2026-10-06", "2026-10-06", "2026-10-07",
             None, None, None),
}
FIELDS = ("last_trade", "halt_start", "halt_end", "base_date", "resume",
          "par_before", "par_after", "price_divisor")


def main() -> int:
    print("四種公告格式的時程解析")
    for code, want in CASES.items():
        got = ca.parse_split_schedule(note(code))
        for f, w in zip(FIELDS, want):
            check(got[f] == w, f"{code}.{f} = {w!r}（實得 {got[f]!r}）")

    print("\n未核備的計畫要標出來（日期還可能變）")
    check(ca.parse_split_schedule(note("5301"))["provisional"] is True,
          "寶得利:「尚未經主管機關核備」→ provisional")
    for c in ("5314", "5904", "6919", "6949"):
        check(ca.parse_split_schedule(note(c))["provisional"] is False,
              f"{c}:已核准的不要誤標 provisional")

    print("\n民國日期的四種寫法")
    for s, w in (("民國114年7月11日", "2025-07-11"), ("114年3月19日", "2025-03-19"),
                 ("114/03/04", "2025-03-04"), ("1150903", "2026-09-03"),
                 ("民國 114年7月15日", "2025-07-15")):
        check(ca.roc_to_iso(s) == w, f"{s} → {w}（實得 {ca.roc_to_iso(s)}）")
    check(ca.roc_to_iso("114年13月45日") is None, "不合法日期回 None 而不是拋例外")

    print("\n主旨篩選:精確樣式會漏掉世紀,所以只認「面額」兩字")
    check(ca.classify("本公司股票面額變更相關事宜") == "split",
          "世紀那種沒有數字沒有期間的標題也要中")
    check(ca.classify("公告本公司股票面額由「新台幣10元」變更為「新台幣1元」") == "split",
          "寶雅/康霈那種完整標題要中")
    check(ca.classify("公告本公司調整減資換發股票作業計畫") == "reduce", "減資要中")
    check(ca.classify("公告本公司董事會決議通過現金增資") is None, "無關公告不要中")

    print("\n去重:世紀同一件事在 3/04~3/28 重貼了 19 則")
    k1 = ca.event_key("5314", "2025-03-04", "本公司股票面額變更相關事宜")
    k2 = ca.event_key("5314", "2025-03-04", "本公司股票面額變更相關事宜 ")
    check(k1 == k2, "尾隨空白不影響去重鍵")
    check(k1 != ca.event_key("5314", "2025-03-05", "本公司股票面額變更相關事宜"),
          "事實發生日不同就是不同事件")

    print("\n停牌區間:公告沒寫時用最後交易日與復牌日推算,並標明來源")
    e = ca.fill_halt({"last_trade": "2026-08-26", "resume": "2026-09-07"})
    check((e["halt_start"], e["halt_end"], e["halt_source"])
          == ("2026-08-27", "2026-09-06", "推算"), "沛爾:推算出 08-27~09-06")
    check(ca.halt_weekdays(e) == 7, f"沛爾停牌橫跨 7 個平日（實得 {ca.halt_weekdays(e)}）")
    e2 = ca.fill_halt({"halt_start": "2025-03-20", "halt_end": "2025-03-28",
                       "last_trade": "2025-03-19", "resume": "2025-03-31"})
    check(e2["halt_source"] == "公告", "公告有寫就不推算,來源標「公告」")
    check(e2["halt_start"] == "2025-03-20", "公告值不被推算值覆蓋")
    check(ca.fill_halt({"resume": "2026-09-07"})["halt_source"] is None,
          "缺最後交易日時不硬湊,來源給 None")
    # 寶得利的公告同時寫了最後交易日與停止買賣期間,兩者獨立算出來一致
    # ——這是對推算邏輯的獨立驗證,別把這條刪了。
    b = ca.parse_split_schedule(note("5301"))
    check(ca.fill_halt(b)["halt_source"] == "公告", "寶得利有寫就用公告值")
    check(ca.fill_halt({"last_trade": b["last_trade"], "resume": b["resume"]})["halt_start"]
          == b["halt_start"], "推算值與寶得利公告值一致（推算邏輯的獨立驗證）")

    print("\n合併:平手時取新的,否則解析器改好了檔案也不會更新")
    t0 = dt.date(2026, 9, 3)
    old_rec = {"_key": "5301|2026-08-24|x", "code": "5301", "fact_date": "2026-08-24",
               "last_trade": "2026-09-29", "halt_start": "2026-09-30",
               "halt_end": "2026-10-06", "resume": "2026-10-07", "halt_source": "推算"}
    new_rec = dict(old_rec, halt_source="公告", base_date="2026-10-06")
    check(merge([old_rec], [new_rec], t0)[0]["halt_source"] == "公告",
          "欄位數相同時新的勝出（寶得利從『推算』升級成『公告』）")
    richer_old = dict(old_rec, par_after=1.0, price_divisor=10)
    thin_new = {"_key": old_rec["_key"], "code": "5301", "fact_date": "2026-08-24",
                "resume": "2026-10-07"}
    check(merge([richer_old], [thin_new], t0)[0].get("par_after") == 1.0,
          "舊記錄嚴格更完整時保留舊的（帶時程表的那則已掉出 feed）")
    stale = {"_key": "9999|2020-01-01|x", "code": "9999", "fact_date": "2020-01-01",
             "resume": "2020-01-10"}
    check(merge([stale], [], t0) == [], "超過保留期的事件會被移除")

    print("\n提前天數")
    t = dt.date(2026, 9, 3)
    check(ca.days_ahead("2026-09-07", t) == 4, "還有 4 天")
    check(ca.days_ahead("2026-08-26", t) == -8, "已過的給負數")
    check(ca.days_ahead(None, t) is None, "沒有日期給 None")

    if fails:
        print(f"\n✗ 公司行動解析測試不通過（{len(fails)} 項）")
        for f in fails:
            print("   ", f)
        return 1
    print(f"\n✓ 公司行動解析測試通過（{len(CASES)} 份真實公告）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
