#!/usr/bin/env python3
"""公司行動的「解析」部分——把交易所公告轉成結構化事件。

抓取在 build_corporate_actions.py;這裡只做純函式,才能離線測試
(tests/test_corp_actions.py 用 tests/fixtures/ 的三份真實公告,不連外網)。

為什麼不用 FinMind:2026-09-03 實測,三個相關 dataset 的未來日期都是 0 筆——
  TaiwanStockDividendResult                  查今天~+60 天 → 0 筆(它是「結果表」)
  TaiwanStockSplitPrice / …CapitalReduction… 查一年內~+90 天 → 未來 0 筆
  TaiwanStockDividend                        有未來除息日,但不帶 data_id 只回 1 檔
FinMind 的分割資料是**生效當天**才出現(寶雅 2026-08-10 720→72),
拿來做回測還原很好用,拿來提早部署提前量是 0 天。

解析上最麻煩的一點:上市與上櫃的「說明」欄格式完全不同。
  上櫃(世紀 5314 / 寶雅 5904)  7.其他應敘明事項:一、換發股票時程如下:
                               (一)舊股票最後交易日:114年3月19日        ← 中文序號、自由文字
  上市(康霈 6919)              4.換發股票時程(1)舊股票最後交易日:民國114年7月11日
                               5.換發股票時程(2)舊股票停止交易期間:…    ← 阿拉伯編號、「民國」前綴
所以不能靠版面位置,只能認標籤文字再往後抓日期。

還有一種情況解析器救不了:強生 4747 的持續公告寫「7.其他應敘明事項:無。」,
時程在**另一則**一次性公告裡(它自己註明「(7)換發股票時程重大訊息公告日期:
115年07月22日」),而那則早就掉出每日 feed。這種只拿得到新股上市日與面額,
停牌區間是 None。累積式抓取從開跑那天起覆蓋率才會完整——這是限制,不是 bug,
所以缺的欄位一律留 None 讓前端顯示「公告未載」,絕不用鄰近事件的值去補。
"""
from __future__ import annotations

import datetime as dt
import re

# 主旨的主濾器只認「面額」兩個字。不能寫得更精確:
#   寶雅/康霈  「公告本公司股票面額由「新台幣10元」變更為「新台幣1元」,公告期間:…」
#   世紀        「本公司股票面額變更相關事宜」  ← 沒有數字、沒有期間,精確樣式會整檔漏掉
SPLIT_SUBJ = re.compile(r"面額")
REDUCE_SUBJ = re.compile(r"減資|換發股票|換發有價證券")
# 條款不能當主濾器:同一件事在兩個市場走不同款次(實測 2026-09-03)
#   面額變更  上櫃第53款 / 上市第51款      減資  上櫃第36款 / 上市第11款
CLAUSE_HINT = {"53": "面額變更", "51": "面額變更", "36": "減資", "11": "其他"}

PROVISIONAL = re.compile(r"尚未經主管機關核備|尚未經核備|核備後內容有所變動|尚未報經")

_ROC_YMD = re.compile(r"(?:民國)?\s*(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_ROC_SLASH = re.compile(r"(?:民國)?\s*(\d{2,3})/(\d{1,2})/(\d{1,2})")
_ROC_COMPACT = re.compile(r"^(\d{3})(\d{2})(\d{2})$")


def roc_to_iso(s: str) -> str | None:
    """民國日期 → ISO。吃 '民國114年7月11日' / '114年3月19日' / '114/03/04' / '1150903'。"""
    s = str(s).strip()
    for pat in (_ROC_YMD, _ROC_SLASH, _ROC_COMPACT):
        m = pat.search(s) if pat is not _ROC_COMPACT else pat.match(s)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            try:
                return dt.date(y + 1911, mo, d).isoformat()
            except ValueError:
                return None
    return None


# 項次編號。三種都要認,因為三家公司用三種寫法(實測):
#   世紀 5314  (一)(二)(三)…            中文序號
#   寶雅 5904  (1)(2)(3)…               阿拉伯序號
#   康霈 6919  4.換發股票時程(1)…       欄位編號 + 阿拉伯序號
# 「(115年8月26日)」不會誤判:括號內是 3 位數字接「年」,兩個樣式都不吃。
_NEXT_ITEM = re.compile(
    r"[（(][一二三四五六七八九十]+[）)]|[（(]\d{1,2}[）)]|\d{1,2}\.[\u4e00-\u9fff]")


def _dates_after(text: str, *labels: str, limit: int = 1) -> list[str]:
    """找標籤後面的民國日期。

    兩個坑,都是實測踩到的:
      * 同一個標籤會出現多次——世紀 5314 的「換發股票基準日」先在「5.發生緣由」
        出現一次(那句沒有日期),真正帶日期的在「7.其他應敘明事項」。只看第一次會漏。
      * 視窗要在下一個項次編號處截斷,否則「停止交易期間」會咬到下一欄
        「最後過戶日」的日期。
    """
    best: list[str] = []
    for lab in labels:
        for m in re.finditer(re.escape(lab), text):
            seg = text[m.end(): m.end() + 200]
            cut = _NEXT_ITEM.search(seg)
            if cut:
                seg = seg[:cut.start()]
            out = [iso for x in _ROC_YMD.finditer(seg) if (iso := roc_to_iso(x.group(0)))]
            if len(out) >= limit:
                return out[:limit]
            if len(out) > len(best):
                best = out
    return best[:limit]


def parse_split_schedule(note: str) -> dict:
    """從「說明」欄抽換發股票時程。缺的欄位給 None,不猜。"""
    # 去掉**所有**空白,含換行:MOPS 的說明欄是固定寬度硬換行,標籤會被切開
    # ——沛爾 6949 的「舊股票\n最後交易日」就是這樣漏掉的。
    t = re.sub(r"\s+", "", str(note))
    last = _dates_after(t, "舊股票最後交易日")
    # 標籤變體:櫃買減資寫「舊股票停止在市場買賣自…起至…止」、「減資換發基準日」
    halt = _dates_after(t, "舊股票停止交易期間", "舊股票停止在市場買賣", limit=2)
    base = _dates_after(t, "換發股票基準日", "減資換發基準日", "減資基準日")
    # 上櫃寫「新股票上櫃買賣日及舊股票終止上櫃買賣日」,上市寫「新股票上市買賣日」
    resume = _dates_after(t, "新股票上市買賣日", "新股票上櫃買賣日", "有價證券換發日")

    par = re.search(r"面額(?:由|新台幣)?[「]?新台幣?([\d.]+)元[」]?變更為[「]?新台幣([\d.]+)元", t) \
        or re.search(r"原每股面額新台幣([\d.]+)元.*?新每股面額新台幣([\d.]+)元", t, re.S)
    div = re.search(r"收盤價之([\d]+)分之1", t)

    return {
        "last_trade": last[0] if last else None,
        "halt_start": halt[0] if halt else None,
        "halt_end": halt[1] if len(halt) > 1 else None,
        "base_date": base[0] if base else None,
        "resume": resume[0] if resume else None,
        "par_before": float(par.group(1)) if par else None,
        "par_after": float(par.group(2)) if par else None,
        "price_divisor": int(div.group(1)) if div else None,
        # 有些換股計畫在公告時還沒過主管機關那關,日期可能再變。
        # 拿來提早部署的行事曆一定要標出來——寶得利 5301 的減資計畫就寫了
        # 「上述換股作業計畫尚未經主管機關核備,若核備後內容有所變動,本公司將另行公告」。
        "provisional": bool(PROVISIONAL.search(t)),
    }


def classify(subject: str, clause: str = "") -> str | None:
    """回傳 'split' / 'reduce' / None。主旨優先,條款只當補充。"""
    s = str(subject)
    if SPLIT_SUBJ.search(s):
        return "split"
    if REDUCE_SUBJ.search(s):
        return "reduce"
    return None


def event_key(code: str, fact_date: str, subject: str) -> str:
    """去重鍵。世紀同一件事在 3/04~3/28 重貼了 19 則,主旨與事實發生日都一樣。"""
    subj = re.sub(r"\s+", "", str(subject))[:40]
    return f"{code}|{fact_date}|{subj}"


def days_ahead(iso: str | None, today: dt.date) -> int | None:
    if not iso:
        return None
    return (dt.date.fromisoformat(iso) - today).days


def fill_halt(e: dict) -> dict:
    """公告沒寫停止交易期間時,用舊股最後交易日與新股上市買賣日推算。

    這不是猜:那兩天之間該公司沒有任何股票可以交易,是定義使然。
    實測驗證——沛爾 6949 的公告只寫了這兩個日期(最後交易 2026-08-26、
    新股上市 2026-09-07),而 2026-08-27~09-03 的日 K 確實一列都沒有。

    公告寫明的與推算出來的用 halt_source 分開,不要混為一談。
    """
    e = dict(e)
    if e.get("halt_start") and e.get("halt_end"):
        e["halt_source"] = "公告"
        return e
    lt, rs = e.get("last_trade"), e.get("resume")
    if lt and rs:
        a = dt.date.fromisoformat(lt) + dt.timedelta(days=1)
        b = dt.date.fromisoformat(rs) - dt.timedelta(days=1)
        if a <= b:
            e["halt_start"], e["halt_end"], e["halt_source"] = a.isoformat(), b.isoformat(), "推算"
            return e
    e["halt_source"] = None
    return e


def halt_weekdays(e: dict) -> int | None:
    """停牌橫跨幾個平日。假日不扣(不查行事曆),所以是上限值,寧可高估。"""
    a, b = e.get("halt_start"), e.get("halt_end")
    if not (a and b):
        return None
    a, b = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return sum(1 for i in range((b - a).days + 1)
               if (a + dt.timedelta(days=i)).weekday() < 5)
