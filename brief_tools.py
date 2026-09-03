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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TPE = dt.timezone(dt.timedelta(hours=8))
ARCHIVE_KEEP = 7
BODY_MAX_HAN = 5000
WEEK_MIN_IN_WEEK = 4        # week_events 非空時,至少要有幾則真的落在本週

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
          "calls": (3, 3), "news": (6, 12), "life": (3, 4), "week_events": (0, 12)}
# positioning.fact 幾乎都是數字,用漢字數量它會誤殺(「46,164.72」是 0 漢字),改數全長。
# 上限存在的理由:定位表一列一個市場,不是把四個市場擠成一句話——
# 上游第 28 期是 12 列各自獨立,我方第 30 期只有 6 列、其中兩列各塞了四個市場。
LIMITS_CHARS = {"positioning.fact": (4, 60)}
REQUIRED = {
    "top3": ("title", "why", "source", "source_url"),
    "positioning": ("market", "fact", "view"),
    "week_events": ("when", "what"),
    "stocks": ("code", "name", "note"),
    "calls": ("title", "basis", "mechanism", "invalid"),
    "news": ("cat", "title", "why"),
    "life": ("cat", "note"),
}
# 讀者不該看到內部欄位名。實例:第 28 期寫了「台股今日無除權息個股(morning.json exdiv 欄為空)」
LEAK = re.compile(r"[A-Za-z][\w-]*\.json|欄位?為空|欄位?是空")
NON_EVENT = re.compile(r"^\s*(今日|本日|本週|當日)?\s*無")   # 「無 X」不是事件

SCHEMA = """content.json 結構（全部欄位必填，括號內為則數，字數為漢字數的下限~上限）:

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
  "quote":       "40~100字，至多 2 段"
}

程式另外會擋（不符一律 exit 1、不寫檔）:
  * 正文 ≤5,000 漢字（超標時會印出各區塊字數，讓你知道要削哪裡）
  * week_events 非空時，至少 4 則的日期要真的落在本週（週一~週日）——
    月中的 FOMC、結算日可以列為額外項目，但不能拿來充數
  * 任何欄位不得出現 json 檔名或「欄為空」這類內部用語
  * week_events 不得寫「無 X」這種非事件
存檔輪替、期號遞增、時戳這三件事由程式處理，你不用管。"""


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
            for f in fields:
                if not str(x.get(f, "")).strip():
                    bad.append(f"{arr}[{i}].{f} 缺漏")

    for path, (lo, hi) in LIMITS_CHARS.items():
        arr, _, field = path.partition(".")
        for i, x in enumerate(c.get(arr) or []):
            n = len(str(x.get(field, "")).strip())
            if not (lo <= n <= hi):
                bad.append(f"{arr}[{i}].{field} {n} 字元，應為 {lo}~{hi}"
                           f"（一列一個市場，數字照寫不要寫成句子）")

    for path, (lo, hi) in LIMITS.items():
        arr, _, field = path.partition(".")
        vals = ([(f"{arr}[{i}].{field}", x.get(field, "")) for i, x in enumerate(c.get(arr) or [])]
                if field else [(arr, c.get(arr, ""))])
        for label, v in vals:
            n = han(v)
            if n < lo:
                bad.append(f"{label} 只有 {n} 字 < 下限 {lo}（寫得太薄）")
            elif n > hi:
                bad.append(f"{label} {n} 字 > 上限 {hi}")

    for i, x in enumerate(c.get("top3") or []):
        if not str(x.get("source_url", "")).startswith("https://"):
            bad.append(f"top3[{i}].source_url 必須是 https:// 開頭的連結")

    evs = c.get("week_events") or []
    if evs:
        hit = sum(1 for e in evs if in_this_week(e.get("when", ""), today))
        if hit < WEEK_MIN_IN_WEEK:
            mon = today - dt.timedelta(days=today.weekday())
            bad.append(f"week_events 只有 {hit} 則落在本週"
                       f"（{mon:%m/%d}~{mon + dt.timedelta(days=6):%m/%d}），"
                       f"至少要 {WEEK_MIN_IN_WEEK} 則")
    for i, e in enumerate(evs):
        if NON_EVENT.match(str(e.get("what", ""))):
            bad.append(f"week_events[{i}].what 是「無 X」的非事件，不要列")

    for arr in REQUIRED:
        for i, x in enumerate(c.get(arr) or []):
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

    return f"""  <header class="masthead">
    <h1>每日晨報</h1>
    <p class="meta">{date} · 第 {edition} 期 · 速讀版 · 產製於 {gen_at}</p>
  </header>

  <nav class="toc"><a href="#sec-three">三件事</a>｜<a href="#sec-pos">定位</a>｜<a href="#sec-week">本週</a>｜<a href="#sec-stocks">個股</a>｜<a href="#sec-calls">判讀</a>｜<a href="#sec-news">要聞</a>｜<a href="#sec-life">生活</a>｜<a href="#sec-final">一句話</a></nav>

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
