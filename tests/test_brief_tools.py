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

    print("\n昨夜美股族群:數字照抄,字串由程式組")
    S = lambda **kw: dict({"g": "光通訊核心", "chg": 3.96, "pos": 58,
                           "vr": 1.82, "t20": -5.9, "view": "仍在修正段"}, **kw)
    def sec(*rows, when="2026-09-02"):
        def mut(c):
            c["us_sectors"] = list(rows)
            c["us_sectors_date"] = when
        return mut

    check(bad_of(sec(S(), S(g="記憶體"))) == [], "兩列合規族群零違規")
    check(bad_of(lambda c: c.pop("us_sectors", None)) == [],
          "整區缺席是合法的（上游沒到就該留空）")

    # 這一區改過四版。前三版都是拿樣式規則管自由文字,三次都被驗收繞過去:
    #   v1 禁字表 7 句漏 6、v2 漢字上限 30 句漏 27、v3 白名單擋不住英文/注音/emoji,
    #   而且 g 欄還留著 v1 那套禁字表,15 句攻擊全過。
    # 下面把三輪累積的攻擊樣本全部收進來——現在它們連欄位都不存在了,
    # 只能塞進 view,而 view 不准有數字、g 必須是固定名單之一。
    # (a) view 唯一真正擋得住的事:自己編的數字。各種進位制都要擋——
    #     第四輪驗收實測 25 種變體漏 18 種,只擋到 Unicode Nd。
    for v in ("仍在修正段，近20日 −5.9%", "仍在修正段，跌了５％", "昨夜跌三趴",
              "退至①分位", "僅剩⅔水位", "跌破Ⅲ級支撐", "近十日走弱",
              "回落兩成", "落在₂₀日線下", "跌²倍", "位置偏低٢٠",
              "跌二十趴", "漲一倍"):
        check(hit(bad_of(sec(S(view=v))), "出現數字"), f"view 夾帶數值被擋：{v}")

    # 要擋的是「看起來像個數值」,不是「數字字元」。第一版訂成後者,
    # 2026-09-21 的 dry-run 當場踩到——「半導體仍偏弱」被擋（半在中文數字表裡）。
    # 這些是族群判讀裡很可能寫出來的話,誤擋一次就是整份晨報 exit 1。
    for v in ("半導體仍偏弱", "與大盤一致", "一路走低", "進一步修正",
              "萬全之策難尋", "一致性偏低", "半數成分走弱", "多週高檔震盪"):
        check(bad_of(sec(S(view=v))) == [], f"正當判讀不被誤擋：{v}")

    # (b) **這些是刻意不擋的,斷言它們通過。**
    #     view 是判讀欄,驗證分不出「熄火」講的是昨夜還是多週——擋它只會
    #     把判讀欄變成不能寫判讀。真正在防的是版面:數字由程式排在 view 旁邊,
    #     寫錯會當場自相矛盾,而且那些數字不是它寫的。
    #     把這件事釘成測試,免得日後有人以為這裡漏擋了。
    for v in ("動能熄火", "人氣散去", "籌碼鬆動", "昨夜買盤全面回籠",
              "momentum fading fast", "ㄊㄨㄟˋ ㄕㄠ", "🚀🚀🚀🔥"):
        check(bad_of(sec(S(view=v))) == [],
              f"單日語氣刻意不擋（靠旁邊的數字自相矛盾，不靠驗證）：{v}")
    check(bad_of(sec(S(view="仍在修正段"))) == [], "正當判讀當然通過")

    print("\n  g 必須照抄，不是自由文字")
    # v3 的 g 用禁字表,15 句判讀全過。改成固定名單就沒有這個問題了。
    for g in ("光通訊崩了", "記憶體無人問津", "半導體資金撤", "記憶體 8 分",
              "網通 dead", "AI／GPU 🚀", "光通訊核心（近20日買盤縮手）", "光通訊核心退燒"):
        check(hit(bad_of(sec(S(g=g))), "不是已知族群名"), f"g 夾帶判讀被擋：{g}")
    for g in ("光通訊核心", "IT 方案／通路", "EDA／IP", "AI／GPU", "光罩"):
        check(bad_of(sec(S(g=g))) == [], f"真實族群名通過：{g}")

    print("\n  數字欄位")
    check(hit(bad_of(sec(S(chg="+3.96%"))), "不是字串"), "chg 寫成字串被擋")
    check(hit(bad_of(sec(S(chg=None))), "必填，不可為 null"),
          "chg 必填（訊息要指出「必填」，不是誤導成型別錯誤）")
    check(hit(bad_of(sec(S(view="近20日仍在修正段"))), "出現數字「20日」"),
          "訊息引整串含單位（報「2」讀起來像沒看懂）")
    check(hit(bad_of(sec(S(view="昨夜跌三趴"))), "出現數字「三趴」"),
          "中文數值也引整串含單位")
    check(bad_of(sec(S(pos=None, vr=None, t20=None))) == [],
          "pos／vr／t20 可以是 null（上游資料不足時就是 null）")
    check(hit(bad_of(sec(S(pos=580))), "超出合理範圍"), "pos 580 被擋（抄錯欄位）")
    check(hit(bad_of(sec(S(chg=396))), "超出合理範圍"), "chg 396 被擋（小數點抄掉）")
    check(hit(bad_of(sec(S(vr=True))), "不是字串"), "布林值被擋（bool 是 int 的子類）")
    # json.loads 預設收 NaN/Infinity,所以這是走得到的路徑。
    # 目前是靠 `lo <= v <= hi` 對 NaN 恆 False 順帶擋住的,把它釘住——
    # 日後若有人改寫這個比較式,NaN 會無聲通過然後在 round() 噴 ValueError。
    import json as _json
    for lit in ("NaN", "Infinity", "-Infinity"):
        v = _json.loads(lit)
        check(hit(bad_of(sec(S(chg=v))), "超出合理範圍"), f"chg={lit} 被擋")
    check("—" in bt.render_body(
        {**base(), "us_sectors": [S(chg=_json.loads("NaN"))],
         "us_sectors_date": "2026-09-02"}, "2026-09-21", 30, "07:52", ""),
        "NaN 真的渲染成破折號，不是 nan")
    check(hit(bad_of(lambda c: (c.__setitem__("us_sectors", ["光通訊核心"]),
                                c.__setitem__("us_sectors_date", "2026-09-02"))), "應該是物件"),
          "字串陣列被擋，不會噴 traceback")
    check(hit(bad_of(lambda c: c["top3"].__setitem__(0, "標題")), "應該是物件"),
          "既有欄位寫成字串陣列也回違規清單，不噴 traceback")

    print("\n  資料日:程式擋,不是 prompt 叮嚀")
    check(hit(bad_of(sec(S(), when="2026-08-20")), "停更"), "13 天前的來源檔被擋")
    check(bad_of(sec(S(), when="2026-08-29")) == [], "5 天（美股連假上限）通過")
    check(hit(bad_of(sec(S(), when="2026-08-28")), "停更"), "6 天被擋")
    check(hit(bad_of(sec(S(), when="2026-09-05")), "未來"), "未來日期另給訊息")
    check(hit(bad_of(lambda c: c.__setitem__("us_sectors", [S()])), "必須給"),
          "有族群卻沒給資料日被擋")
    for v in ("20260902", "2026/09/02", "9/2", "2026-9-2", "2026-W36-2",
              "2026-09-02T00:00:00", "2026-02-29", "2026-13-01"):
        check(hit(bad_of(sec(S(), when=v)), "不是合法"), f"格式／日期錯被擋：{v}")
    for v in (20260902, 20260902.0, None, ["2026-09-02"], True):
        c = base(); c["us_sectors"] = [S()]; c["us_sectors_date"] = v
        check(bt.validate(c, TODAY) != [], f"非字串型別被擋：{v!r}")

    print("\n  版面")
    check(hit(bad_of(sec(*[S() for _ in range(7)])), "0~6"), "7 列被擋")
    c = base(); sec(S(), S(g="半導體設備", chg=5.27, pos=98, vr=1.88, t20=-4.9,
                           view="位置仍低"))(c)
    b = bt.render_body(c, "2026-09-21", 30, "07:52", "")
    check("sec-sectors" in b and "昨夜美股族群" in b, "有族群時區塊出現")
    check('href="#sec-sectors"' in b, "有族群時目錄也出現")
    check("2026-09-02" in b, "資料日渲染進標題（讀者要知道「昨夜」是哪一夜）")
    # 「昨夜」那一格由程式組,模型沒有機會把它寫成敘述
    check("+3.96%／收在區間 58%／量比 1.82" in b, "昨夜欄由程式用數字排出來")
    check("-5.9%" in b, "近20日欄依上游精度印 1 位小數（印 2 位是虛假精度）")
    nb = bt.render_body({**c, "us_sectors": [S(pos=None, vr=None, t20=None)]},
                        "2026-09-21", 30, "07:52", "")
    check("—" in nb, "null 顯示破折號，不是 0")
    b0 = bt.render_body(base(), "2026-09-21", 30, "07:52", "")
    check("sec-sectors" not in b0 and "昨夜美股族群" not in b0, "無族群時整區不出現")
    check('href="#sec-sectors"' not in b0, "無族群時目錄也不留死連結")
    check(len(bt.section_han(b0)) == 8 and len(bt.section_han(b)) == 9,
          f"區塊數 無族群 8／有族群 9（實得 {len(bt.section_han(b0))}／{len(bt.section_han(b))}）")

    # 正文現況 4,688/5,000。這一區的字數上界全在 view（24 字元）與 g（固定名單,
    # 最長 8 字元）——數字欄位不佔漢字。前一版因為 day 可以刷重複標籤,
    # 實際上界 626 漢字、會讓整份晨報 exit 1 不寫檔;文件卻寫 206。
    longest_g = max(bt.SECTOR_NAMES, key=lambda g: bt.han(g))
    worst = base()
    sec(*[S(g=longest_g, view="字" * 24) for _ in range(6)])(worst)
    check(bad_of(lambda c: c.update(worst)) == [], "最壞情況仍合法")
    wb = bt.render_body(worst, "2026-09-21", 30, "07:52", "")
    worst_han = bt.section_han(wb)["sectors"]
    check(worst_han <= 260,
          f"最壞情況族群區 {worst_han} 漢字 ≤ 260（有硬上界，撐不爆總預算）")
    # 原本這條拿 base() 合成內容當「正文」(2,535 漢字),餘裕 2,241——
    # 而真實正文是 4,600 出頭,餘裕只有兩三百。合成樣本證明不了這件事。
    # 改讀 repo 裡真的那一期,用 cmd_check 同一套算法。
    import re as _re
    live = (ROOT / "daily-brief.html")
    if live.exists():
        trunk = _re.sub(r'<section class="archive">.*', "",
                        live.read_text(encoding="utf-8"), flags=_re.S)
        cur = bt.han(trunk) - bt.section_han(trunk).get("sectors", 0)
        check(cur + worst_han <= bt.BODY_MAX_HAN,
              f"真實正文（不含族群區）{cur} + 最壞族群區 {worst_han} "
              f"≤ {bt.BODY_MAX_HAN}，餘裕 {bt.BODY_MAX_HAN - cur - worst_han}")
    else:
        check(False, "找不到 daily-brief.html，無法用真實正文驗預算")

    if fails:
        print(f"\n✗ 晨報預算測試不通過（{len(fails)} 項）")
        for f in fails:
            print("   ", f)
        return 1
    print("\n✓ 晨報預算測試通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
