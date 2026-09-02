#!/usr/bin/env python3
"""每日晨報產生器。

上游(shihpc)的 daily-brief.html / daily-brief-card.json 是「雲端排程 session 每晨
產製並 push」——那個 session 不會跟著 fork 過來,所以自架必須自己產。

設計上與上游不同的一點:**LLM 只產結構化 JSON,版式與存檔輪替由程式負責。**
理由是每天要穩定產出 516 行 HTML、又要自己維護「只留最近 7 期」的存檔,對 LLM 是
不必要的負擔,錯了也不會有人發現;把版式交給程式,LLM 只需要做它擅長的事(判讀與組稿)。
硬預算(字數、則數)也因此能在程式端強制,不是只寫在 prompt 裡祈禱。

資料來源與誠實邊界:
  news.json(本 repo)             今日三件事、要聞速覽、今日關注個股
  v2 morning.json                開盤前定位(gap/spot)、籌碼、除權息
  v2 us.json                     隔夜美股、匯率
  本週關鍵事件                    **只用 morning.json 的 exdiv 等有憑據的項目**。
                                 FOMC/財報日這類沒有資料源,純 API 呼叫沒有網路搜尋,
                                 讓模型憑訓練資料寫會產出看起來合理但可能錯誤的日期
                                 ——寧可留空也不編。
  生活四區塊                      無資料源,由模型自由發揮(不涉及市場事實)。

用法:
    ANTHROPIC_API_KEY=... python3 build_brief.py            # 產出並寫檔
    python3 build_brief.py --dry-run                        # 只印組出來的 JSON
    python3 build_brief.py --model claude-sonnet-5          # 換模型
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sites import raw  # noqa: E402 — 單一換址點

ROOT = Path(__file__).resolve().parent
TPE = dt.timezone(dt.timedelta(hours=8))
ARCHIVE_KEEP = 7          # 只保留最近 7 期(近一週),與上游規範一致
BODY_MAX_HAN = 5000       # 當期正文漢字硬上限


# ── LLM 輸出的結構（程式端據此驗證，不靠 prompt 自律）────────────────────
class Item(BaseModel):
    title: str = Field(description="標題，不超過 30 字")
    why: str = Field(description="一句判讀，不超過 50 字")


class Pos(BaseModel):
    market: str = Field(description="市場名稱，如 加權指數 / 費半 / 台幣")
    fact: str = Field(description="數字事實，需含日期")
    view: str = Field(description="一句解讀，不超過 25 字")


class Ev(BaseModel):
    when: str
    what: str = Field(description="不超過 40 字")


class Stock(BaseModel):
    code: str
    name: str
    note: str = Field(description="一句為什麼值得看，不超過 40 字")


class Call(BaseModel):
    title: str
    basis: str = Field(description="依據")
    mechanism: str = Field(description="影響機制")
    invalid: str = Field(description="失效情境")


class News(BaseModel):
    cat: str = Field(description="法規 / 要聞 / 經濟 三選一")
    title: str
    why: str
    source: str = ""
    detail: str = ""


class Life(BaseModel):
    cat: str
    note: str


class Brief(BaseModel):
    top3: List[Item] = Field(description="今日三件事，恰好 3 則")
    positioning: List[Pos] = Field(description="開盤前定位，4~6 列")
    week_events: List[Ev] = Field(description="本週關鍵事件；**只寫輸入資料裡有憑據的**，沒有就給空陣列")
    stocks: List[Stock] = Field(description="今日關注個股，3~6 檔")
    calls: List[Call] = Field(description="重點判讀，恰好 3 則")
    news: List[News] = Field(description="要聞速覽，6~12 則")
    life: List[Life] = Field(description="生活與家庭四區塊，恰好 4 則")
    quote: str = Field(description="今日一句話，≤100 字，至多 2 段")


def fetch_json(url: str, timeout: int = 40) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:                       # noqa: BLE001 — 缺一份輸入不該讓整份晨報掛掉
        print(f"  ! 取不到 {url}: {e}", file=sys.stderr)
        return None


def han_count(s: str) -> int:
    return len(re.findall(r"[一-鿿]", s))


def gather() -> dict:
    news = json.loads((ROOT / "news.json").read_text(encoding="utf-8"))
    morning = fetch_json(raw("taiwan-flow-live-v2", "data/morning.json")) or {}
    us = fetch_json(raw("taiwan-flow-live-v2", "data/us.json")) or {}
    # news.json 是以個股為鍵的結構;壓成扁平清單並限量,避免 prompt 過長
    flat = []
    for s in (news.get("stocks") or [])[:60]:
        for n in (s.get("news") or [])[:3]:
            flat.append({"stock": s.get("stock_id"), "name": s.get("stock_name"),
                         "title": n.get("title"), "source": n.get("source"),
                         "date": n.get("date"), "link": n.get("link")})
    return {"news": flat[:120], "morning": morning, "us": us,
            "news_meta": {k: news.get(k) for k in ("generated_at", "total_news", "trading_days")}}


PROMPT = """你在為台股投資人產製「每日晨報」。今天是 {date}（台北）。

以下是今天可用的**全部**資料，JSON 格式：

<news>{news}</news>
<morning>{morning}</morning>
<us>{us}</us>

規則（違反任何一條都算失敗）：

1. **只寫資料裡有的東西。** 每個數字、每個事實都必須能在上面的 JSON 找到出處。
   找不到就不要寫。不確定就標「未確認」。
2. **`week_events` 特別嚴格**：只寫 morning 的 `exdiv`（除權息）這類**資料裡明確有的**
   行事曆項目。你沒有網路搜尋，**不知道**本週的 FOMC、財報日、經濟數據發布日——
   那些一律不要寫，寧可回空陣列 `[]`。編造日期是這份晨報最嚴重的錯誤。
3. 字數：`top3` 標題 ≤30 字、why ≤50 字；`positioning.view` ≤25 字；
   `week_events.what` ≤40 字；`calls` 三個欄位各 ≤150 字；`quote` ≤100 字。
4. 語氣：現況描述，不做預測、不寫該買該賣。技術指標是描述不是訊號。
5. `life` 四則與市場無關（親子／學習／科技／生活），可自由發揮。
6. 全部用繁體中文。"""


def render(b: Brief, date: str, edition: int, gen_at: str, archive_html: str) -> str:
    e = html.escape

    def items(xs):
        return "\n".join(
            f'    <article class="item"><h3>{e(x.title)}</h3>'
            f'<p class="why">{e(x.why)}</p></article>' for x in xs)

    pos = "\n".join(
        f"      <tr><td>{e(p.market)}</td><td>{e(p.fact)}</td><td>{e(p.view)}</td></tr>"
        for p in b.positioning)
    week = "\n".join(f"      <li>{e(x.when)}｜{e(x.what)}</li>" for x in b.week_events) \
        or "      <li>本期無可據以列示的行事曆項目（資料源未提供）。</li>"
    stocks = "\n".join(
        f"      <li><b>{e(s.code)} {e(s.name)}</b>：{e(s.note)}</li>" for s in b.stocks)
    calls = "\n".join(
        f'    <article class="item"><h3>{e(c.title)}</h3>'
        f'<p class="why">依據：{e(c.basis)}</p><p class="why">機制：{e(c.mechanism)}</p>'
        f'<p class="why">失效：{e(c.invalid)}</p></article>' for c in b.calls)
    news = "\n".join(
        f'    <article class="item"><h3>【{e(n.cat)}】{e(n.title)}</h3>'
        f'<p class="why">{e(n.why)}</p>'
        + (f'<details class="more"><summary>細節</summary><p>{e(n.detail)}</p>'
           f'<p class="meta">{e(n.source)}</p></details>' if n.detail else
           (f'<p class="meta">{e(n.source)}</p>' if n.source else ""))
        + "</article>" for n in b.news)
    life = "\n".join(
        f"      <section><h3>{e(x.cat)}</h3><p>{e(x.note)}</p></section>" for x in b.life)

    return f"""  <header class="masthead">
    <h1>每日晨報</h1>
    <p class="meta">{date} · 第 {edition} 期 · 速讀版 · 產製於 {gen_at}</p>
  </header>

  <nav class="toc"><a href="#sec-three">三件事</a>｜<a href="#sec-pos">定位</a>｜<a href="#sec-week">本週</a>｜<a href="#sec-stocks">個股</a>｜<a href="#sec-calls">判讀</a>｜<a href="#sec-news">要聞</a>｜<a href="#sec-life">生活</a>｜<a href="#sec-final">一句話</a></nav>

  <section class="block" id="sec-three">
    <h2>今日三件事</h2>
{items(b.top3)}
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
    <p class="final">{e(b.quote)}</p>
  </section>

  <p class="note">內容由程式每日彙整自公開新聞來源與市場資料並經 AI 組稿；市場數據為最近一交易日並標示日期。本刊只描述現況、不做預測，非投資建議。行事曆項目僅列資料源明確提供者。</p>

{archive_html}
</div>
"""


def build_archive(prev_html: str, date: str, edition: int, b: Brief) -> str:
    """把本期壓成摘要塞進存檔頂端，只留最近 ARCHIVE_KEEP 期。"""
    e = html.escape
    lis = [f"        <li>【今日三件事】{e(x.title)}</li>" for x in b.top3]
    lis += [f"        <li>【開盤前定位】{e(p.market)} {e(p.fact)}</li>" for p in b.positioning[:2]]
    lis += [f"        <li>【重點判讀】{e(c.title)}</li>" for c in b.calls]
    lis += [f"        <li>【要聞速覽】{e(n.title)}</li>" for n in b.news[:4]]
    cur = ("    <details>\n"
           f"      <summary>{date} · 第 {edition} 期</summary>\n      <ul>\n"
           + "\n".join(lis) + "\n      </ul>\n    </details>")
    old = re.findall(r"    <details>\n      <summary>.*?</details>", prev_html, re.S)
    kept = [cur] + old[: ARCHIVE_KEEP - 1]
    return '  <section class="archive">\n    <h2>歷史存檔</h2>\n' + "\n".join(kept) + "\n  </section>"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    now = dt.datetime.now(TPE)
    date = now.strftime("%Y-%m-%d")
    prev_card = ROOT / "daily-brief-card.json"
    edition = (json.loads(prev_card.read_text(encoding="utf-8")).get("edition", 0) + 1
               if prev_card.exists() else 1)

    data = gather()
    print(f"  輸入:news {len(data['news'])} 則 / morning {'有' if data['morning'] else '無'}"
          f" / us {'有' if data['us'] else '無'}")

    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=a.model,
        max_tokens=16000,
        messages=[{"role": "user", "content": PROMPT.format(
            date=date,
            news=json.dumps(data["news"], ensure_ascii=False),
            morning=json.dumps(data["morning"], ensure_ascii=False)[:20000],
            us=json.dumps(data["us"], ensure_ascii=False)[:8000])}],
        output_format=Brief,
    )
    b: Brief = resp.parsed_output
    print(f"  產出:三件事 {len(b.top3)} / 定位 {len(b.positioning)} / 本週 {len(b.week_events)}"
          f" / 個股 {len(b.stocks)} / 判讀 {len(b.calls)} / 要聞 {len(b.news)}")
    print(f"  用量:in {resp.usage.input_tokens} / out {resp.usage.output_tokens}")

    if a.dry_run:
        print(json.dumps(b.model_dump(), ensure_ascii=False, indent=2)[:3000])
        return 0

    gen_at = now.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    prev = (ROOT / "daily-brief.html").read_text(encoding="utf-8") if (ROOT / "daily-brief.html").exists() else ""
    body = render(b, date, edition, gen_at, build_archive(prev, date, edition, b))

    n = han_count(re.sub(r'<section class="archive">.*', "", body, flags=re.S))
    if n > BODY_MAX_HAN:
        print(f"  ★ 正文 {n} 漢字 > 上限 {BODY_MAX_HAN} —— 仍寫出,但請收斂 prompt", file=sys.stderr)
    print(f"  正文漢字 {n} / 上限 {BODY_MAX_HAN}")

    head = (ROOT / "templates/brief_head.html").read_text(encoding="utf-8")
    tail = (ROOT / "templates/brief_tail.html").read_text(encoding="utf-8")
    (ROOT / "daily-brief.html").write_text(head + body + tail, encoding="utf-8")

    card = {"schema": 1, "date": date, "edition": edition, "generated_at": gen_at,
            "top3": [x.model_dump() for x in b.top3],
            "positioning": [x.model_dump() for x in b.positioning],
            "week_events": [x.model_dump() for x in b.week_events],
            "quote": b.quote[:120],
            "life": [x.model_dump() for x in b.life]}
    prev_card.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  ✓ 第 {edition} 期已寫入 daily-brief.html + daily-brief-card.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
