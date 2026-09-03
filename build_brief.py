#!/usr/bin/env python3
"""每日晨報產生器——**備援路徑**，直接呼叫 Anthropic API。

常態產製是排程 cloud session(台北一~五 07:52,見 README「每日晨報」節);
這支是它掛掉時用 workflow_dispatch 手動補的後路。

兩條路徑共用 `brief_tools.py` 的 validate/render/write:
版式、字數上下限、存檔輪替只有一份實作,備援跑出來的版式不會跟常態的分岔。
(先前這裡自帶一份 render 複本,改版式時只改一邊就會悄悄長歪。)

資料來源與誠實邊界:
  news.json(本 repo)             今日三件事、要聞速覽、今日關注個股
  v2 morning.json                開盤前定位(gap/spot)、籌碼、除權息
  v2 us.json                     隔夜美股、匯率
  本週關鍵事件                    **這條路徑沒有網路搜尋**,不知道本週的 FOMC、財報日、
                                 經濟數據發布日,讓模型憑訓練資料寫會產出看起來合理但
                                 可能錯誤的日期——寧可留空也不編。
                                 (brief_tools 的「至少 4 則落在本週」只在非空時才要求,
                                 所以留空在這條路徑是合法的。)
  生活三~四區塊                   無資料源,由模型自由發揮(不涉及市場事實)。

top3 的 source_url 只能用輸入資料裡帶的 link,不能自己拼——這條路徑無法驗證連結。

用法:
    ANTHROPIC_API_KEY=... python3 build_brief.py            # 產出並寫檔
    python3 build_brief.py --dry-run                        # 只印組出來的 JSON 與校驗結果
    python3 build_brief.py --model claude-sonnet-5          # 換模型
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brief_tools                      # noqa: E402 — 版式與校驗的唯一實作
from sites import raw                   # noqa: E402 — 單一換址點

ROOT = Path(__file__).resolve().parent
TPE = dt.timezone(dt.timedelta(hours=8))


# ── LLM 輸出的結構；字數敘述與 brief_tools.LIMITS 一致，實際強制在 validate ──────
class Item(BaseModel):
    title: str = Field(description="標題，12~30 字")
    why: str = Field(description="【判讀】這件事對今天台股的意義，40~90 字；不是把標題換句話說")
    source: str = Field(description="來源名稱，如 中央社 / 經濟日報")
    source_url: str = Field(description="https:// 開頭的連結，只能用輸入資料裡的 link，不可自行拼湊")


class Pos(BaseModel):
    market: str = Field(description="市場名稱，如 加權指數 / 費半 / 台幣")
    fact: str = Field(description="數字事實，需含日期")
    view: str = Field(description="一句解讀，10~25 字")


class Ev(BaseModel):
    when: str = Field(description="如 週五 9/4 20:30")
    what: str = Field(description="12~40 字")


class Stock(BaseModel):
    code: str
    name: str
    note: str = Field(description="一句為什麼值得看，16~40 字")


class Call(BaseModel):
    title: str
    basis: str = Field(description="依據，50~150 字")
    mechanism: str = Field(description="影響機制，50~150 字")
    invalid: str = Field(description="失效情境，40~150 字")


class News(BaseModel):
    cat: str = Field(description="法規 / 要聞 / 經濟 三選一")
    title: str
    why: str = Field(description="16~60 字")
    source: str = ""
    detail: str = ""


class Life(BaseModel):
    cat: str = Field(description="區塊名，如 政策與權益 / 健康與生活 / 下一代")
    note: str = Field(description="200~500 字的完整段落，不是一句話")


class Brief(BaseModel):
    top3: List[Item] = Field(description="今日三件事，恰好 3 則")
    positioning: List[Pos] = Field(description="開盤前定位，4~6 列")
    week_events: List[Ev] = Field(description="本週關鍵事件；**只寫輸入資料裡有憑據的**，沒有就給空陣列")
    stocks: List[Stock] = Field(description="今日關注個股，3~6 檔")
    calls: List[Call] = Field(description="重點判讀，恰好 3 則")
    news: List[News] = Field(description="要聞速覽，6~12 則")
    life: List[Life] = Field(description="生活與家庭，3~4 區塊，每塊 200~500 字")
    quote: str = Field(description="今日一句話，40~100 字，至多 2 段")


def fetch_json(url: str, timeout: int = 40) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:                       # noqa: BLE001 — 缺一份輸入不該讓整份晨報掛掉
        print(f"  ! 取不到 {url}: {e}", file=sys.stderr)
        return None


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
   要寫就至少 4 則且日期落在本週，湊不到就整個留空。
3. **字數是上下限，兩端都會擋。** 寫太薄跟寫太長一樣算失敗：
   `top3.title` 12~30、`top3.why` 40~90、`positioning.view` 10~25、
   `week_events.what` 12~40、`stocks.note` 16~40、`news.why` 16~60、
   `calls` 三欄各 50~150（invalid 40~150）、**`life.note` 200~500**、`quote` 40~100。
   `life` 每塊是**完整段落**，不是一句話。
4. `top3.source_url` 只能填 `<news>` 裡該則新聞自帶的 `link`，不可自行拼湊網址。
5. 語氣：現況描述，不做預測、不寫該買該賣。技術指標是描述不是訊號。
6. `life` 三~四塊與市場無關（政策與權益／健康與生活／下一代／科技與生活），可自由發揮。
7. 讀者看不到這些 JSON，**不要在內容裡提 json 檔名或欄位名**，也不要寫「無 X」這種非事件。
8. 全部用繁體中文。"""

REPAIR = """\n\n你上一版有以下違規，請重出完整 JSON（不是只給 diff），並修掉每一條：\n{bad}"""


def ask(client, model: str, prompt: str) -> Brief:
    resp = client.messages.parse(
        model=model, max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
        output_format=Brief,
    )
    print(f"  用量:in {resp.usage.input_tokens} / out {resp.usage.output_tokens}")
    return resp.parsed_output


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    date = dt.datetime.now(TPE).strftime("%Y-%m-%d")
    data = gather()
    print(f"  輸入:news {len(data['news'])} 則 / morning {'有' if data['morning'] else '無'}"
          f" / us {'有' if data['us'] else '無'}")

    prompt = PROMPT.format(
        date=date,
        news=json.dumps(data["news"], ensure_ascii=False),
        morning=json.dumps(data["morning"], ensure_ascii=False)[:20000],
        us=json.dumps(data["us"], ensure_ascii=False)[:8000])

    import anthropic
    client = anthropic.Anthropic()
    b = ask(client, a.model, prompt)
    c = b.model_dump()
    bad = brief_tools.validate(c)
    print(f"  產出:三件事 {len(b.top3)} / 定位 {len(b.positioning)} / 本週 {len(b.week_events)}"
          f" / 個股 {len(b.stocks)} / 判讀 {len(b.calls)} / 要聞 {len(b.news)}"
          f" / 生活 {len(b.life)} — 違規 {len(bad)} 條")

    # 一次修補回合。無人看顧的路徑,把違規清單回饋給模型比直接掛掉划算(多一次呼叫 ≈ $0.2)。
    if bad and not a.dry_run:
        for x in bad:
            print("   ·", x, file=sys.stderr)
        print("  → 帶著違規清單重出一次", file=sys.stderr)
        c = ask(client, a.model, prompt + REPAIR.format(bad="\n".join(f"- {x}" for x in bad))
                ).model_dump()

    if a.dry_run:
        for x in bad:
            print("   ·", x)
        print(json.dumps(c, ensure_ascii=False, indent=2)[:3000])
        return 0

    return brief_tools.write(c)


if __name__ == "__main__":
    raise SystemExit(main())
