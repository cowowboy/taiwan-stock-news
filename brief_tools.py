#!/usr/bin/env python3
"""每日晨報的「程式該負責的部分」——版式、存檔輪替、硬預算校驗。

為什麼要有這支:晨報由排程 Claude session 產製(見 README「每日晨報」節)。
session 擅長的是判讀與組稿;不擅長、也沒必要每天重做的是:
  * 516 行 HTML 的版式(每天重寫一次就有一次寫歪的機會)
  * 「歷史存檔只留最近 7 期」的輪替(靠記得 = 遲早會忘)
  * 「正文 ≤5,000 漢字」等硬預算(寫在 prompt 裡是祈禱,不是保證)
把這三件事交給程式,session 只要產出結構化 JSON。

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
LIMITS = {                      # 欄位 → 字數上限(漢字數),與 README 的產製規範一致
    "top3.title": 30, "top3.why": 50, "positioning.view": 25,
    "week_events.what": 40, "calls.basis": 150, "calls.mechanism": 150,
    "calls.invalid": 150, "quote": 100,
}

SCHEMA = """content.json 結構（全部欄位必填，陣列長度見括號）:

{
  "top3":        [{"title": "≤30字", "why": "≤50字"}]                    (恰好 3)
  "positioning": [{"market": "", "fact": "含日期的數字事實", "view": "≤25字"}]  (4~6)
  "week_events": [{"when": "", "what": "≤40字"}]                         (0~12，無憑據就給 [])
  "stocks":      [{"code": "", "name": "", "note": "≤40字"}]             (3~6)
  "calls":       [{"title": "", "basis": "≤150", "mechanism": "≤150", "invalid": "≤150"}] (恰好 3)
  "news":        [{"cat": "法規|要聞|經濟", "title": "", "why": "", "source": "", "detail": ""}] (6~12)
  "life":        [{"cat": "", "note": ""}]                               (恰好 4)
  "quote":       "≤100字，至多 2 段"
}

程式會強制：存檔只留最近 7 期、正文 ≤5,000 漢字（超標時 exit 1）、
期號自動遞增、時戳自動填。這些你不用管。"""


def han(s: str) -> int:
    return len(re.findall(r"[一-鿿]", str(s)))


def esc(s) -> str:
    return html.escape(str(s))


def validate(c: dict) -> list[str]:
    """回傳違規清單。空清單＝通過。"""
    bad = []
    counts = {"top3": (3, 3), "positioning": (4, 6), "stocks": (3, 6),
              "calls": (3, 3), "news": (6, 12), "life": (4, 4), "week_events": (0, 12)}
    for k, (lo, hi) in counts.items():
        n = len(c.get(k) or [])
        if not (lo <= n <= hi):
            bad.append(f"{k} 應為 {lo}~{hi} 則，實際 {n}")
    for path, lim in LIMITS.items():
        if "." in path:
            arr, field = path.split(".")
            for i, x in enumerate(c.get(arr) or []):
                if han(x.get(field, "")) > lim:
                    bad.append(f"{arr}[{i}].{field} {han(x.get(field,''))} 字 > {lim}")
        elif han(c.get(path, "")) > lim:
            bad.append(f"{path} {han(c.get(path,''))} 字 > {lim}")
    return bad


def render_body(c: dict, date: str, edition: int, gen_at: str, archive: str) -> str:
    def arts(xs, t="title", w="why"):
        return "\n".join(f'    <article class="item"><h3>{esc(x[t])}</h3>'
                         f'<p class="why">{esc(x[w])}</p></article>' for x in xs)

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
{arts(c["top3"])}
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


def cmd_render() -> int:
    c = json.load(sys.stdin)
    bad = validate(c)
    if bad:
        print("★ 內容不符規範，未寫檔：", file=sys.stderr)
        for b in bad:
            print("   ", b, file=sys.stderr)
        return 1

    now = dt.datetime.now(TPE)
    date, gen_at = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    card_p, html_p = ROOT / "daily-brief-card.json", ROOT / "daily-brief.html"
    edition = (json.loads(card_p.read_text(encoding="utf-8")).get("edition", 0) + 1
               if card_p.exists() else 1)
    prev = html_p.read_text(encoding="utf-8") if html_p.exists() else ""

    body = render_body(c, date, edition, gen_at, rotate_archive(prev, date, edition, c))
    n = han(re.sub(r'<section class="archive">.*', "", body, flags=re.S))
    if n > BODY_MAX_HAN:
        print(f"★ 正文 {n} 漢字 > 上限 {BODY_MAX_HAN}，未寫檔", file=sys.stderr)
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
    print(f"✓ 第 {edition} 期  正文 {n} 漢字  "
          f"存檔 {arch.group(0).count('<summary>') if arch else 0} 期")
    return 0


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
    ok = n <= BODY_MAX_HAN and eds <= ARCHIVE_KEEP and "postMessage" in s
    print(f"  正文 {n} 漢字（上限 {BODY_MAX_HAN}）")
    print(f"  存檔 {eds} 期（上限 {ARCHIVE_KEEP}）")
    print(f"  自動高度 script：{'保留' if 'postMessage' in s else '★ 遺失'}")
    print("✓ 通過" if ok else "★ 不通過")
    return 0 if ok else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    raise SystemExit({"render": cmd_render, "check": cmd_check,
                      "schema": lambda: (print(SCHEMA), 0)[1]}.get(cmd, cmd_check)())
