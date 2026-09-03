#!/usr/bin/env python3
# tests/test_brief_tools.py
# 晨報硬預算的回歸測試。
#
# 由來:2026-09-03 第 28 期與上游同日並排,正文 3,464 vs 5,064 漢字,
# 1,545 字的落差全部集中在生活區塊——因為 schema 從頭到尾只寫「≤N 字」,
# 模型就貼著上限以下寫。只有上限沒有下限,寫得太薄不會被任何人擋下來。
# 這支把「上下限、必填欄、本週事件真的在本週、不洩漏內部欄位名」變成 CI 會擋的失敗。
#
# 執行:python tests/test_brief_tools.py
import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brief_tools as bt  # noqa: E402

TODAY = dt.date(2026, 9, 3)          # 週四;當週為 8/31(一) ~ 9/6(日)
fails: list[str] = []


def z(n: int) -> str:
    return "字" * n


def base() -> dict:
    return {
        "top3": [{"title": z(20), "why": z(60), "source": "中央社",
                  "source_url": "https://example.com/a"} for _ in range(3)],
        "positioning": [{"market": f"市場{i}", "fact": "9/2 收 46,164.72", "view": z(15)}
                        for i in range(6)],
        "week_events": [{"when": f"9/{d} 20:30", "what": z(20)} for d in (1, 2, 3, 4)],
        "stocks": [{"code": "2330", "name": "台積電", "note": z(25)} for _ in range(3)],
        "calls": [{"title": "標題", "basis": z(80), "mechanism": z(80), "invalid": z(60)}
                  for _ in range(3)],
        "news": [{"cat": "要聞", "title": "標題", "why": z(30)} for _ in range(6)],
        "life": [{"cat": "政策與權益", "note": z(300)} for _ in range(3)],
        "quote": z(60),
    }


def check(cond: bool, label: str) -> None:
    print(f"  {'✓' if cond else '✗'} {label}")
    if not cond:
        fails.append(label)


def bad_of(mut=None) -> list[str]:
    c = base()
    if mut:
        mut(c)
    return bt.validate(c, TODAY)


def hit(bad: list[str], needle: str) -> bool:
    return any(needle in b for b in bad)


def main() -> int:
    print("字數上下限")
    check(bad_of() == [], f"合規內容零違規（實得 {bad_of()}）")
    check(hit(bad_of(lambda c: c["life"].__setitem__(
        0, {"cat": "政策與權益", "note": z(21)})), "下限"),
        "life.note 21 字（第 28 期的實際寫法）被下限擋下")
    check(hit(bad_of(lambda c: c["life"].__setitem__(
        0, {"cat": "政策與權益", "note": z(900)})), "上限"), "life.note 900 字被上限擋下")
    check(hit(bad_of(lambda c: c["calls"][0].update(basis=z(10))), "下限"),
          "calls.basis 寫太薄被擋")

    print("\n開盤前定位:列數與 fact 用字元數(不是漢字)")
    check(hit(bad_of(lambda c: c.__setitem__("positioning", c["positioning"][:4])), "6~10"),
          "只有 4 列被擋（一列一個市場，第 30 期把四個市場擠成一列）")
    check(bad_of(lambda c: c["positioning"].extend(
        [{"market": f"額外{i}", "fact": "406.96", "view": z(15)} for i in range(4)])) == [],
        "10 列合規")
    check(hit(bad_of(lambda c: c["positioning"][0].update(
        fact="標普 +0.46%、那斯達克 +0.45%、道瓊 +0.56%、費半 +0.45%、"
             "台積電 ADR 415.5 美元、新台幣 31.74 元")), "字元"),
        "一列塞多個市場（>60 字元）被擋")
    check(bad_of(lambda c: c["positioning"][0].update(fact="46,164.72")) == [],
          "純數字 fact 通過（漢字數 0，若用漢字下限會誤殺）")

    print("\n必填欄與來源連結")
    check(hit(bad_of(lambda c: c["top3"][0].pop("source_url")), "缺漏"),
          "top3 缺 source_url 被擋")
    check(hit(bad_of(lambda c: c["top3"][0].update(source_url="www.a.com")), "https://"),
          "top3.source_url 非 https 連結被擋")
    check(hit(bad_of(lambda c: c["top3"][0].update(source="  ")), "缺漏"),
          "top3.source 只有空白視同缺漏")

    print("\n本週關鍵事件真的要在本週")
    check(bt.in_this_week("週五 9/4 20:30", TODAY) and bt.in_this_week("8/31（一）", TODAY),
          "9/4、8/31 認得是本週")
    check(not bt.in_this_week("9/16（三）", TODAY) and not bt.in_this_week("9/10 前", TODAY),
          "9/16、9/10 認得不是本週")
    check(not bt.in_this_week("今日", TODAY), "沒有日期的 when 不算本週")
    check(hit(bad_of(lambda c: c.__setitem__("week_events", [
        {"when": "9/3", "what": z(20)}, {"when": "9/16", "what": z(20)},
        {"when": "9/10", "what": z(20)}])), "落在本週"),
        "3 則裡只有 1 則在本週（第 28 期是 7 則裡 2 則）被擋")
    check(bad_of(lambda c: c.__setitem__("week_events", [])) == [],
          "留空是合法的（build_brief.py 無網搜時的唯一誠實選項）")

    print("\n內部用語不得外洩")
    for leak in ("morning.json exdiv 欄為空", "news.json 沒有這檔的資料", "該欄位為空"):
        check(hit(bad_of(lambda c, s=leak: c["week_events"][0].update(what=s + z(12))), "洩漏"),
              f"「{leak}」被擋")
    check(hit(bad_of(lambda c: c["week_events"][0].update(
        what="今日無除權息個股需要留意的標的")), "非事件"), "「今日無…」的非事件被擋")

    print("\n版式")
    body = bt.render_body(base(), "2026-09-03", 28, "2026-09-03T08:00:00+08:00", "")
    check(body.count("【判讀】") == 3, "三件事各有一段【判讀】")
    check(body.count("來源：<a") == 3, "三件事各有一條來源連結")
    check(all(f"<h3>{i}. " in body for i in (1, 2, 3)), "三件事有編號")
    sec = bt.section_han(body)
    check(set(sec) == {"three", "pos", "week", "stocks", "calls", "news", "life", "final"},
          f"section_han 認得八個區塊（實得 {sorted(sec)}）")
    check(sec.get("life", 0) >= 900,
          f"life 內層 <section> 不會咬掉外層計數（實得 {sec.get('life')}）")
    check(bt.han(re.sub(r'<section class="archive">.*', "", body, flags=re.S))
          <= bt.BODY_MAX_HAN, "合規內容仍在正文總預算內")

    print("\n上下限本身要可同時滿足")
    floor = sum(lo * n for (path, (lo, _)), n in (
        (("top3.title", bt.LIMITS["top3.title"]), 3),
        (("top3.why", bt.LIMITS["top3.why"]), 3),
        (("calls.basis", bt.LIMITS["calls.basis"]), 3),
        (("calls.mechanism", bt.LIMITS["calls.mechanism"]), 3),
        (("calls.invalid", bt.LIMITS["calls.invalid"]), 3),
        (("life.note", bt.LIMITS["life.note"]), bt.COUNTS["life"][0]),
        (("news.why", bt.LIMITS["news.why"]), bt.COUNTS["news"][0]),
        (("stocks.note", bt.LIMITS["stocks.note"]), bt.COUNTS["stocks"][0]),
        (("positioning.view", bt.LIMITS["positioning.view"]), bt.COUNTS["positioning"][0]),
        (("quote", bt.LIMITS["quote"]), 1)))
    check(floor <= bt.BODY_MAX_HAN * 0.6,
          f"所有下限相加 {floor} 字，離總上限 {bt.BODY_MAX_HAN} 還有餘裕（不會鎖死）")

    if fails:
        print(f"\n✗ 晨報預算測試不通過（{len(fails)} 項）")
        for f in fails:
            print("   ", f)
        return 1
    print("\n✓ 晨報預算測試通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
