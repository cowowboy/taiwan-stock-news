#!/usr/bin/env python3
"""每日晨報的「程式該負責的部分」——版式、存檔輪替、硬預算校驗。

為什麼要有這支:晨報由排程 Claude session 產製(見 README「每日晨報」節)。
session 擅長的是判讀與組稿;不擅長、也沒必要每天重做的是:
  * 500+ 行 HTML 的版式(每天重寫一次就有一次寫歪的機會)
  * 「歷史存檔只留最近 7 期」的輪替(靠記得 = 遲早會忘)
  * 字數上下限這類硬預算(寫在 prompt 裡是祈禱,不是保證)
把這三件事交給程式,session 只要產出結構化 JSON。

字數為什麼要有**下限**:2026-09-03 第 28 期與上游同日比對,正文 3,464 vs 5,064 漢字,
其中 1,545 字的落差集中在生活區塊——因為 schema 從頭到尾只寫「≤N 字」,
模型自然貼著上限以下寫,結果只用掉預算的 69%。上限防的是寫太多,
下限防的是寫太少;兩邊都要擋,這份晨報才會穩定有份量。

用法(session 產完內容後呼叫):
    python3 brief_tools.py render  < content.json     # 產出兩個檔
    python3 brief_tools.py check                      # 只校驗現有檔案
    python3 brief_tools.py schema                     # 印出 content.json 的格式說明

content.json 的結構見 `schema` 子指令;欄位與 daily-brief-card.json 對齊。
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import unicodedata
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TPE = dt.timezone(dt.timedelta(hours=8))
ARCHIVE_KEEP = 7
BODY_MAX_HAN = 5000
WEEK_MIN_IN_WEEK = 4        # week_events 非空時,至少要有幾則真的落在本週
# 美股週一假日（MLK／總統日／勞動節…）時,台北週二晨報引用的是上週五＝4 天,
# 正好卡在門檻上。留 5 天,晨報或上游晚一天產出時才不會逢連假就誤擋整區。
STALE_MAX_DAYS = 5
DATE_FMT = re.compile(r"\d{4}-\d{2}-\d{2}")

LIMITS = {                  # 欄位 → (下限, 上限) 漢字數;兩端都擋
    "top3.title": (12, 30), "top3.why": (40, 90),
    "positioning.view": (10, 25),
    "week_events.what": (12, 40),
    "stocks.note": (16, 40),
    "calls.basis": (50, 150), "calls.mechanism": (50, 150), "calls.invalid": (40, 150),
    "news.why": (16, 60),

    "life.note": (200, 500),
    "quote": (40, 100),
}
COUNTS = {"top3": (3, 3), "positioning": (6, 10), "stocks": (3, 6),
          "calls": (3, 3), "news": (6, 12), "life": (3, 4), "week_events": (0, 12),
          "us_sectors": (0, 6)}
# positioning.fact 幾乎都是數字,用漢字數量它會誤殺(「46,164.72」是 0 漢字),改數全長。
# 上限存在的理由:定位表一列一個市場,不是把四個市場擠成一句話——
# 上游第 28 期是 12 列各自獨立,我方第 30 期只有 6 列、其中兩列各塞了四個市場。
CHAR_HINT = {  # 字元數違規時附的提示語,不同欄位要給不同方向
    "positioning.fact": "（一列一個市場，數字照寫不要寫成句子）",
    "us_sectors.view": "（一句位置判讀，數字不用寫，程式會自己排）",
}
LIMITS_CHARS = {"positioning.fact": (4, 60),
                # view 是這一區唯一的自由文字,字數上界也全靠它——
                # g 來自固定名單、其餘都是數字,所以族群區的最壞字數是有界的。
                "us_sectors.view": (4, 24)}
REQUIRED = {
    "top3": ("title", "why", "source", "source_url"),
    "positioning": ("market", "fact", "view"),
    "week_events": ("when", "what"),
    "stocks": ("code", "name", "note"),
    "calls": ("title", "basis", "mechanism", "invalid"),
    "news": ("cat", "title", "why"),
    "life": ("cat", "note"),
    "us_sectors": ("g", "view"),
}
# 讀者不該看到內部欄位名。實例:第 28 期寫了「台股今日無除權息個股(morning.json exdiv 欄為空)」
LEAK = re.compile(r"[A-Za-z][\w-]*\.json|欄位?為空|欄位?是空")
NON_EVENT = re.compile(r"^\s*(今日|本日|本週|當日)?\s*無")   # 「無 X」不是事件
# ── 昨夜美股族群 ──────────────────────────────────────────────
# 由來:2026-09-17 光通訊核心 −0.08%／收在區間 10%,被依單日指標寫成「熄火」;
# 9/18 就是 +3.96%／收在區間 58%。一天的數字撐不起趨勢敘述。
#
# **這一區的數字不讓模型用文字重打,照抄上游的數值,字串由程式組。**
# 前三版都是拿樣式規則去管自由文字,三次都被繞過去,而且是同一個原因——
# 寫的人永遠比過濾器有更多表達空間:
#   v1 禁字表   「熄火」擋掉就寫「退燒」「人氣散去」「籌碼鬆動」。7 句漏 6 句。
#   v2 漢字上限 一句判決只要 4~6 個漢字,而真實寫法本身就用掉 6 個,
#               兩者在長度這個維度上不可分離。30 句漏 27 句。
#   v3 白名單   擋得住漢字,擋不住英文/注音/emoji/相容漢字;而且 g 欄還在用
#               v1 那套禁字表,15 句攻擊 15 句全過。
# 所以改掉問題本身:chg/pos/vr/t20 是數字欄位（照抄上游）,g 必須是上游那組
# 固定名稱之一,只有 view 是自由文字。
# 附帶好處:全形半形、分隔符號、字數失控、標籤重複這些問題全部消失。
#
# **誠實說清楚這一區擋得住什麼**:view 仍然寫得出「動能熄火」——那是判讀欄,
# 本來就該能寫判讀,驗證分不出「熄火」是講昨夜還是講多週。真正在防的是
# **版面**:chg/pos/vr 與 t20 就排在 view 旁邊,寫錯會當場自相矛盾,
# 而且那些數字不是它寫的。這是「讓錯誤顯眼」,不是「讓錯誤不可能」。
# 驗證只保證一件事:view 裡不會出現**它自己編的數字**。

# 上游 taiwan-flow-live-v2 的 src/build_us_sectors.py 的 SECTORS 定義。
# 上游加族群時這裡要跟著加——**刻意做成顯式耦合**:族群名是照抄的,
# 不是自由填的,寧可少一組也不要讓這欄變回自由文字。
SECTOR_NAMES = {
    "光通訊核心", "光通訊上游", "AI／GPU", "半導體設備", "記憶體", "類比／功率",
    "封測", "材料／基板", "連接器", "EDA／IP", "網通", "IT 方案／通路", "光罩",
}
# view 裡的數字偵測。原本用 \d 只擋到 Unicode Nd,「跌三趴」「退至①分位」
# 「僅⅔」「Ⅲ級支撐」全部漏掉——而「跌三趴」正好就是要擋的那種夾帶。
# Nd/Nl/No 三類涵蓋阿拉伯、全形、羅馬、圈號、上下標、分數;中文數字另外列。
# 代價是「一路走低」這種慣用語也會被擋。這個取捨是刻意的:
# 誤擋只會得到一則可修的違規訊息,漏擋會讓錯的數字上線。
CJK_NUM = "〇一二三四五六七八九十百千萬億兩半參壹貳叁肆伍陸柒捌玖拾佰仟"


def _is_num(ch: str) -> bool:
    return ch in CJK_NUM or unicodedata.category(ch) in ("Nd", "Nl", "No")


def find_number(s: str) -> str:
    """回傳第一串連續數字字元,沒有就回空字串。

    引整串而不是單一字元:「近20日」報「出現數字 2」讀起來像沒看懂,
    報「出現數字 20」才指得到人看得出來的東西。
    """
    for i, ch in enumerate(s):
        if _is_num(ch):
            j = i
            while j < len(s) and _is_num(s[j]):
                j += 1
            return s[i:j]
    return ""


# 數字欄位的合理範圍。擋的是「抄錯欄位」「抄成字串」,不是抄錯值——
# 後者沒有辦法在這裡驗,只能靠 prompt 要求照抄。
NUM_RANGE = {"chg": (-50.0, 50.0), "pos": (0.0, 100.0),
             "vr": (0.0, 50.0), "t20": (-95.0, 500.0)}

SCHEMA_TMPL = """content.json 結構（括號內為則數；未標「字元」者為漢字數的下限~上限）。
us_sectors 可整區缺席，其餘欄位必填：

{
  "top3": [{                                                             (恰好 3)
      "title":      "12~30字",
      "why":        "40~90字。這是【判讀】——說明這件事對今天的台股意味著什麼，"
                    "不是把標題換句話說一次",
      "source":     "來源名稱，如 博通官方新聞稿 / 證交所",
      "source_url": "https:// 開頭的可點連結"
  }]
  "positioning": [{"market": "", "fact": "含日期的數字事實，4~60 字元", "view": "10~25字"}]  (6~10)
                 一列一個市場（加權／櫃買／成交金額／台積電／三大法人／新台幣／
                 美股／費半／美債／原油…），不要把數個市場擠進同一列
  "week_events": [{"when": "如 週五 9/4 20:30", "what": "12~40字"}]        (0~12)
  "stocks":      [{"code": "", "name": "", "note": "16~40字"}]            (3~6)
  "calls":       [{"title": "", "basis": "50~150",
                   "mechanism": "50~150", "invalid": "40~150"}]           (恰好 3)
  "news":        [{"cat": "法規|要聞|經濟", "title": "",
                   "why": "16~60", "source": "", "detail": ""}]           (6~12)
  "life":        [{"cat": "", "note": "200~500字"}]                       (3~4)
  "us_sectors":  [{"g": "族群名", "chg": 3.96, "pos": 58, "vr": 1.82,
                   "t20": -5.9, "view": "4~24字元的一句判讀"}]           (0~6)
  "us_sectors_date": "來源檔的資料日 YYYY-MM-DD"（us_sectors 非空時必填）
                 資料源：taiwan-flow-live-v2 的 data/us_sector_flow.json。
                 **g 與四個數字一律照抄該檔，不要改寫、不要自己算**：
                   g    必須是下列之一（程式比對固定名單，一字不改）：
                        {sector_names}
                        來源檔若出現名單外的族群，代表 brief_tools.py 要補，
                        先把那一組跳過、照常出刊，不要改寫名稱去硬湊。
                   chg  當日漲跌%（必填）　pos 收在當日區間%　vr 量比
                   t20  近 20 日報酬%　　　資料不足時上游給 null，照抄 null
                   數字要是 JSON 數值，不是字串（"3.96" 會被擋）。
                   合理範圍 chg ±50、pos 0~100、vr 0~50、t20 −95~500，
                   超出代表抄錯欄位。
                 **挑哪幾組**：當日 chg 最高兩組、最低兩組，再加上與「今日關注
                 個股」對得上的組，至多 6 組。不要只挑支持某個敘事的組。
                 **版面上的「昨夜」那一格是程式用這些數字排的，你不用寫。**
                   → 所以不會有「+3.96%／收在區間 58%／量比 1.82」這種欄位，
                     也不要把數字寫進 view。
                 view 是這區唯一的自由文字，寫一句位置判讀，如「仍在修正段」。
                   它旁邊就是 t20，所以這裡本來就該寫多週的位置，不是昨夜的漲跌。
                   **不准出現任何數字**——阿拉伯、全形、中文數字（三、十、兩）、
                   羅馬數字、圈號①、上下標、分數½ 都算。連帶會擋掉「一路走低」
                   這種慣用語，換個寫法即可。
                 該檔的 date 若不是最近一個美股交易日，整區留空（寧缺勿舊）；
                 us_sectors_date 距今超過 5 天，程式會擋。
                 validated=false 的族群成分尚未驗證，view 要保守寫。
                 reliable=false（組內離散大或成分缺漏）代表組平均不具代表性。
  "quote":       "40~100字，至多 2 段"
}

程式另外會擋（不符一律 exit 1、不寫檔）:
  * 正文 ≤5,000 漢字（超標時會印出各區塊字數，讓你知道要削哪裡）
  * week_events 非空時，至少 4 則的日期要真的落在本週（週一~週日）——
    月中的 FOMC、結算日可以列為額外項目，但不能拿來充數
  * 任何欄位不得出現 json 檔名或「欄為空」這類內部用語
  * week_events 不得寫「無 X」這種非事件
存檔輪替、期號遞增、時戳這三件事由程式處理，你不用管。"""


SCHEMA = SCHEMA_TMPL.replace(
    "{sector_names}", "、".join(sorted(SECTOR_NAMES)))


def han(s: str) -> int:
    return len(re.findall(r"[一-鿿]", str(s)))


def esc(s) -> str:
    return html.escape(str(s))


def in_this_week(when: str, today: dt.date) -> bool:
    """when 欄裡的 M/D 是否落在 today 所屬的週一~週日。抓不到日期算不在。"""
    m = re.search(r"(\d{1,2})/(\d{1,2})", str(when))
    if not m:
        return False
    mon = today - dt.timedelta(days=today.weekday())
    for y in (today.year, today.year - 1, today.year + 1):
        try:
            d = dt.date(y, int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        if mon <= d <= mon + dt.timedelta(days=6):
            return True
    return False


def validate(c: dict, today: dt.date | None = None) -> list[str]:
    """回傳違規清單。空清單＝通過。"""
    today = today or dt.datetime.now(TPE).date()
    bad = []

    for k, (lo, hi) in COUNTS.items():
        n = len(c.get(k) or [])
        if not (lo <= n <= hi):
            bad.append(f"{k} 應為 {lo}~{hi} 則，實際 {n}")

    for arr, fields in REQUIRED.items():
        for i, x in enumerate(c.get(arr) or []):
            # 模型把陣列寫成字串陣列時,以前會在這裡噴 AttributeError traceback。
            # 違規清單才是它修得動的東西,traceback 不是。
            if not isinstance(x, dict):
                bad.append(f"{arr}[{i}] 應該是物件，實得 {type(x).__name__}")
                continue
            for f in fields:
                if not str(x.get(f, "")).strip():
                    bad.append(f"{arr}[{i}].{f} 缺漏")

    for path, (lo, hi) in LIMITS_CHARS.items():
        arr, _, field = path.partition(".")
        for i, x in enumerate(c.get(arr) or []):
            if not isinstance(x, dict):
                continue                      # 型別問題已由 REQUIRED 迴圈回報
            n = len(str(x.get(field, "")).strip())
            if not (lo <= n <= hi):
                bad.append(f"{arr}[{i}].{field} {n} 字元，應為 {lo}~{hi}"
                           + CHAR_HINT.get(path, ""))

    for path, (lo, hi) in LIMITS.items():
        arr, _, field = path.partition(".")
        vals = ([(f"{arr}[{i}].{field}", x.get(field, ""))
                 for i, x in enumerate(c.get(arr) or []) if isinstance(x, dict)]
                if field else [(arr, c.get(arr, ""))])
        for label, v in vals:
            n = han(v)
            if n < lo:
                bad.append(f"{label} 只有 {n} 漢字 < 下限 {lo}（寫得太薄）")
            elif n > hi:
                bad.append(f"{label} {n} 漢字 > 上限 {hi}"
                           + CHAR_HINT.get(path, ""))

    for i, x in enumerate(c.get("top3") or []):
        if not isinstance(x, dict):
            continue                          # 型別問題已由 REQUIRED 迴圈回報
        if not str(x.get("source_url", "")).startswith("https://"):
            bad.append(f"top3[{i}].source_url 必須是 https:// 開頭的連結")

    evs = c.get("week_events") or []
    if evs:
        hit = sum(1 for e in evs
                   if isinstance(e, dict) and in_this_week(e.get("when", ""), today))
        if hit < WEEK_MIN_IN_WEEK:
            mon = today - dt.timedelta(days=today.weekday())
            bad.append(f"week_events 只有 {hit} 則落在本週"
                       f"（{mon:%m/%d}~{mon + dt.timedelta(days=6):%m/%d}），"
                       f"至少要 {WEEK_MIN_IN_WEEK} 則")
    for i, e in enumerate(evs):
        if isinstance(e, dict) and NON_EVENT.match(str(e.get("what", ""))):
            bad.append(f"week_events[{i}].what 是「無 X」的非事件，不要列")

    if c.get("us_sectors"):
        raw = c.get("us_sectors_date")
        ds = str(raw).strip() if isinstance(raw, str) else ""
        # 先全字串比對再 parse:date.fromisoformat 連 "20260902" 和 "2026-W36-2"
        # 都收,而 str(20260902.0) 會把 .0 吞掉——等於非字串型別完全沒擋到。
        d0 = None
        if DATE_FMT.fullmatch(ds):
            try:
                d0 = dt.date.fromisoformat(ds)
            except ValueError:
                d0 = None
        if not str(raw or "").strip():
            bad.append("有 us_sectors 就必須給 us_sectors_date（來源檔的資料日，"
                       "YYYY-MM-DD），否則沒人擋得住拿舊資料當昨夜")
        elif d0 is None:
            bad.append(f"us_sectors_date「{raw}」不是合法的 YYYY-MM-DD 日期")
        elif (age := (today - d0).days) < 0:
            bad.append(f"us_sectors_date {ds} 是未來日期（比今天晚 {-age} 天）")
        elif age > STALE_MAX_DAYS:
            bad.append(f"us_sectors_date {ds} 距今 {age} 天——這是來源停更了，整區請留空")

    for i, x in enumerate(c.get("us_sectors") or []):
        if not isinstance(x, dict):
            bad.append(f"us_sectors[{i}] 不是物件")
            continue
        if (g := str(x.get("g", "")).strip()) not in SECTOR_NAMES:
            bad.append(f"us_sectors[{i}].g「{g}」不是已知族群名"
                       f"——照抄來源檔的 g 欄，不要改寫、不要加註")
        for f, (lo, hi) in NUM_RANGE.items():
            v = x.get(f)
            if v is None:
                # pos/vr/t20 上游資料不足時就是 null,照抄 null 是對的;chg 不行。
                if f != "chg":
                    continue
                bad.append(f"us_sectors[{i}].chg 必填，不可為 null"
                           f"（pos／vr／t20 才可以是 null）")
            elif isinstance(v, bool) or not isinstance(v, (int, float)):
                bad.append(f"us_sectors[{i}].{f} 要照抄來源檔的數值，"
                           f"不是字串或文字（實得 {v!r}）")
            elif not (lo <= v <= hi):
                bad.append(f"us_sectors[{i}].{f} = {v} 超出合理範圍 {lo}~{hi}，"
                           f"是不是抄錯欄位")
        # view 是唯一的自由文字,而它旁邊就是 t20——判讀寫在這裡是對的。
        # 但數字不准出現:數字都有自己的欄位,寫進 view 就是繞過照抄。
        if ch := find_number(str(x.get("view", ""))):
            bad.append(f"us_sectors[{i}].view 出現數字「{ch}」"
                       f"——數字都有自己的欄位，這裡只寫一句判讀")

    for arr in REQUIRED:
        for i, x in enumerate(c.get(arr) or []):
            if not isinstance(x, dict):
                continue                      # 型別問題已由 REQUIRED 迴圈回報
            for f, v in x.items():
                if m := LEAK.search(str(v)):
                    bad.append(f"{arr}[{i}].{f} 洩漏內部用語「{m.group(0)}」")
    return bad


def section_han(body: str) -> dict[str, int]:
    """各 sec-* 區塊的漢字數。用 class=\"block\" 切,才不會被 life 裡的內層 section 咬到。"""
    out = {}
    for p in re.split(r'(?=<section class="block" id=")', body):
        if m := re.match(r'<section class="block" id="sec-([a-z]+)"', p):
            out[m.group(1)] = han(p)
    return out


def render_body(c: dict, date: str, edition: int, gen_at: str, archive: str) -> str:
    top3 = "\n".join(
        '    <article class="item">\n'
        f'      <h3>{i}. {esc(x["title"])}</h3>\n'
        f'      <p class="why">【判讀】{esc(x["why"])}</p>\n'
        f'      <p class="meta">來源：<a href="{esc(x["source_url"])}" '
        f'target="_blank" rel="noopener">{esc(x["source"])}</a></p>\n'
        '    </article>' for i, x in enumerate(c["top3"], 1))
    pos = "\n".join(f"      <tr><td>{esc(p['market'])}</td><td>{esc(p['fact'])}</td>"
                    f"<td>{esc(p['view'])}</td></tr>" for p in c["positioning"])
    week = "\n".join(f"      <li>{esc(x['when'])}｜{esc(x['what'])}</li>"
                     for x in c.get("week_events") or []) \
        or "      <li>本期無可據以列示的行事曆項目（資料源未提供）。</li>"
    stocks = "\n".join(f"      <li><b>{esc(s['code'])} {esc(s['name'])}</b>：{esc(s['note'])}</li>"
                       for s in c["stocks"])
    calls = "\n".join(f'    <article class="item"><h3>{esc(x["title"])}</h3>'
                      f'<p class="why">依據：{esc(x["basis"])}</p>'
                      f'<p class="why">機制：{esc(x["mechanism"])}</p>'
                      f'<p class="why">失效：{esc(x["invalid"])}</p></article>' for x in c["calls"])
    news = "\n".join(
        f'    <article class="item"><h3>【{esc(n["cat"])}】{esc(n["title"])}</h3>'
        f'<p class="why">{esc(n["why"])}</p>'
        + (f'<details class="more"><summary>細節</summary><p>{esc(n["detail"])}</p>'
           f'<p class="meta">{esc(n.get("source",""))}</p></details>' if n.get("detail")
           else (f'<p class="meta">{esc(n["source"])}</p>' if n.get("source") else ""))
        + "</article>" for n in c["news"])
    life = "\n".join(f"      <section><h3>{esc(x['cat'])}</h3><p>{esc(x['note'])}</p></section>"
                     for x in c["life"])
    # 族群區可以整個不存在（上游檔缺席或過期）。缺就連標題帶目錄一起不出現,
    # 不要留一個空殼區塊讓讀者以為當天沒有族群輪動。
    secs = c.get("us_sectors") or []
    sectors_date = f"（{esc(c.get('us_sectors_date', ''))}）" if secs else ""
    sectors_nav = '<a href="#sec-sectors">族群</a>｜' if secs else ""

    def num(v, unit="%", sign=True, nd=2):
        """數字欄的統一排版。上游資料不足會給 null,顯示破折號而不是 0。"""
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v:
            return "—"                        # v != v 擋 NaN
        return f"{v:+.{nd}f}{unit}" if sign else f"{v:.{nd}f}{unit}"

    # 「昨夜」這一格由程式組,模型只照抄數字。前三版讓模型自己寫這格,
    # 三次都被寫成趨勢敘述——問題不在規則不夠嚴,在它本來就不該是自由文字。
    sectors_block = (f"""
  <section class="block" id="sec-sectors">
    <h2>昨夜美股族群{sectors_date}</h2>
    <div class="poswrap">
    <table class="pos"><thead><tr><th>族群</th><th>昨夜</th><th>近20日</th><th>位置</th></tr></thead>
      <tbody>
""" + "\n".join(
        f"      <tr><td>{esc(x['g'])}</td>"
        f"<td>{num(x.get('chg'))}／收在區間 {num(x.get('pos'), '%', sign=False, nd=0)}"
        f"／量比 {num(x.get('vr'), '', sign=False)}</td>"
        # t20 上游是 1 位小數（build_us_sectors.py 的 round(...,1)）,
        # 印 2 位是虛假精度
        f"<td>{num(x.get('t20'), nd=1)}</td><td>{esc(x['view'].strip())}</td></tr>"
        for x in secs) + """
      </tbody></table>
    </div>
  </section>
""") if secs else ""

    return f"""  <header class="masthead">
    <h1>每日晨報</h1>
    <p class="meta">{date} · 第 {edition} 期 · 速讀版 · 產製於 {gen_at}</p>
  </header>

  <nav class="toc"><a href="#sec-three">三件事</a>｜<a href="#sec-pos">定位</a>｜{sectors_nav}<a href="#sec-week">本週</a>｜<a href="#sec-stocks">個股</a>｜<a href="#sec-calls">判讀</a>｜<a href="#sec-news">要聞</a>｜<a href="#sec-life">生活</a>｜<a href="#sec-final">一句話</a></nav>

  <section class="block" id="sec-three">
    <h2>今日三件事</h2>
{top3}
  </section>

  <section class="block" id="sec-pos">
    <h2>開盤前定位</h2>
    <table class="pos"><thead><tr><th>市場</th><th>數字</th><th>解讀</th></tr></thead>
      <tbody>
{pos}
      </tbody></table>
  </section>

{sectors_block}
  <section class="block" id="sec-week">
    <h2>本週關鍵事件</h2>
    <ul class="cal">
{week}
    </ul>
  </section>

  <section class="block" id="sec-stocks">
    <h2>今日關注個股</h2>
    <ul class="stocks">
{stocks}
    </ul>
  </section>

  <section class="block" id="sec-calls">
    <h2>重點判讀</h2>
{calls}
  </section>

  <section class="block" id="sec-news">
    <h2>要聞速覽</h2>
{news}
  </section>

  <section class="block" id="sec-life">
    <details class="lifeblock"><summary>生活與家庭</summary>
{life}
    </details>
  </section>

  <section class="block" id="sec-final">
    <h2>今日一句話</h2>
    <p class="final">{esc(c["quote"])}</p>
  </section>

  <p class="note">內容每日彙整自公開新聞來源與市場資料；市場數據為最近一交易日並標示日期。本刊只描述現況、不做預測，非投資建議。行事曆項目僅列有明確來源者。</p>

{archive}
</div>
"""


def rotate_archive(prev_html: str, date: str, edition: int, c: dict) -> str:
    lis = [f"        <li>【今日三件事】{esc(x['title'])}</li>" for x in c["top3"]]
    lis += [f"        <li>【開盤前定位】{esc(p['market'])} {esc(p['fact'])}</li>"
            for p in c["positioning"][:2]]
    lis += [f"        <li>【重點判讀】{esc(x['title'])}</li>" for x in c["calls"]]
    lis += [f"        <li>【要聞速覽】{esc(n['title'])}</li>" for n in c["news"][:4]]
    cur = ("    <details>\n"
           f"      <summary>{date} · 第 {edition} 期</summary>\n      <ul>\n"
           + "\n".join(lis) + "\n      </ul>\n    </details>")
    arch = re.search(r'<section class="archive">.*?</section>', prev_html, re.S)
    old = re.findall(r"    <details>\n      <summary>.*?</details>",
                     arch.group(0) if arch else "", re.S)
    return ('  <section class="archive">\n    <h2>歷史存檔</h2>\n'
            + "\n".join([cur] + old[: ARCHIVE_KEEP - 1]) + "\n  </section>")


def write(c: dict) -> int:
    """校驗 → 排版 → 寫檔。排程 session 與 build_brief.py 走的是同一條路,版式不會分岔。"""
    now = dt.datetime.now(TPE)
    bad = validate(c, now.date())
    if bad:
        print("★ 內容不符規範，未寫檔：", file=sys.stderr)
        for b in bad:
            print("   ", b, file=sys.stderr)
        return 1

    date, gen_at = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    card_p, html_p = ROOT / "daily-brief-card.json", ROOT / "daily-brief.html"
    edition = (json.loads(card_p.read_text(encoding="utf-8")).get("edition", 0) + 1
               if card_p.exists() else 1)
    prev = html_p.read_text(encoding="utf-8") if html_p.exists() else ""

    body = render_body(c, date, edition, gen_at, rotate_archive(prev, date, edition, c))
    trunk = re.sub(r'<section class="archive">.*', "", body, flags=re.S)
    n = han(trunk)
    if n > BODY_MAX_HAN:
        print(f"★ 正文 {n} 漢字 > 上限 {BODY_MAX_HAN}，未寫檔。各區塊字數：", file=sys.stderr)
        for k, v in sorted(section_han(trunk).items(), key=lambda kv: -kv[1]):
            print(f"    {k:8s} {v:5d}", file=sys.stderr)
        return 1

    head = (ROOT / "templates/brief_head.html").read_text(encoding="utf-8")
    tail = (ROOT / "templates/brief_tail.html").read_text(encoding="utf-8")
    html_p.write_text(head + body + tail, encoding="utf-8")
    card_p.write_text(json.dumps({
        "schema": 1, "date": date, "edition": edition, "generated_at": gen_at,
        "top3": c["top3"], "positioning": c["positioning"],
        "week_events": c.get("week_events") or [],
        "quote": c["quote"][:120], "life": c["life"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 只數存檔區塊內的 <summary>；新聞的 details.more 與 lifeblock 也有 summary
    arch = re.search(r'<section class="archive">.*?</section>', body, re.S)
    print(f"✓ 第 {edition} 期  正文 {n}/{BODY_MAX_HAN} 漢字  "
          f"存檔 {arch.group(0).count('<summary>') if arch else 0} 期")
    print("  區塊：" + "  ".join(f"{k}={v}" for k, v in section_han(trunk).items()))
    return 0


def cmd_render() -> int:
    return write(json.load(sys.stdin))


def cmd_check() -> int:
    p = ROOT / "daily-brief.html"
    if not p.exists():
        print("找不到 daily-brief.html", file=sys.stderr)
        return 1
    s = p.read_text(encoding="utf-8")
    body = re.sub(r'<section class="archive">.*', "", s, flags=re.S)
    arch = re.search(r'<section class="archive">.*?</section>', s, re.S)
    eds = arch.group(0).count("<summary>") if arch else 0
    n = han(body)
    # 判讀與來源只數三件事區塊內的:要聞區也有 <p class="meta">來源,一起數會虛胖
    three = next((p for p in re.split(r'(?=<section class="block" id=")', body)
                  if p.startswith('<section class="block" id="sec-three"')), "")
    judged, linked = three.count("【判讀】"), three.count("來源：<a")
    ok = (n <= BODY_MAX_HAN and eds <= ARCHIVE_KEEP
          and "postMessage" in s and judged == 3 and linked == 3)
    print(f"  正文 {n} 漢字（上限 {BODY_MAX_HAN}）")
    print("  區塊：" + "  ".join(f"{k}={v}" for k, v in section_han(body).items()))
    print(f"  存檔 {eds} 期（上限 {ARCHIVE_KEEP}）")
    print(f"  三件事判讀 {judged}/3、來源連結 {linked}/3")
    print(f"  自動高度 script：{'保留' if 'postMessage' in s else '★ 遺失'}")
    print("✓ 通過" if ok else "★ 不通過")
    return 0 if ok else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    raise SystemExit({"render": cmd_render, "check": cmd_check,
                      "schema": lambda: (print(SCHEMA), 0)[1]}.get(cmd, cmd_check)())
