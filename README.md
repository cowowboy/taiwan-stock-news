# 台股新聞（taiwan-stock-news）

股市雷達 Hub 的子專案。每日抓取 FinMind `TaiwanStockNews`，套**來源白名單**過濾
（排除論壇、內容農場、綜合社會媒體），輸出 `news.json` 給前端 dashboard 呈現。

線上：https://shihpc.github.io/taiwan-stock-news/

`news.json` 的 `generated_at`（台北 +08:00）／`trading_days`／新聞 `date` 等日期欄語意，
以及跨站產出檔的日期對照，見 postmkt repo 的 `docs/date-semantics.md`（五 repo 統一對照表）。

## 個股追蹤（第一批：基本面，2026-07-19）

`index.html` 第四個 tab「個股追蹤」（純前端插入式，`#trackView`＋`loadTrack`/`renderTrack` 等
`trk*` 函式群，三站同步函式零改動）。**兩組來源、視覺區分**：

- **自選股**：localStorage key `news_watchlist`（代號陣列），輸入框可增/刪（格式驗證，存在性由
  Worker 回傳判定，查不到顯示提示不炸）。
- **持股診斷股**：讀同 origin 的 `pm_holdings`（postmkt 存，格式 `[{c,sh,...}]`），**本站唯讀**
  （不可增刪，管理權留 postmkt），標註「來自持股診斷」。

點選任一檔 → 下方三區塊（基本面）：① 每日新聞追蹤（讀本站 `news.json`，僅 ~150 檔法人熱門股，
不在池內誠實降級標註）② 每月營收追蹤（最新月＋MoM＋YoY＋近 12 月柱狀＋近 14 日公布標「新公布」，
億元）③ 每季財報追蹤（EPS/營收/毛利率/營益率/淨利率/稅後淨利 × 本季/上季QoQ/去年同季YoY＋近 8 季
迷你趨勢，紅增綠減）。

**資料架構**：月營收/季財報走 v2 Worker `/fundamentals?ids=`（`USW_WORKER` 常數，即時代理 FinMind，
任何台股皆可）；前端**絕不碰 FinMind token**。QoQ/YoY 由 Worker 算並附回。詳端點規格見
taiwan-flow-live-v2 `PROJECT_SUMMARY.md`「快速接手」。

## 個股追蹤（第二批：籌碼面，2026-07-20）

選中股詳情框改為 `基本面 / 籌碼面` **分頁**（`TRACK.view`＋`.trk-vtabs`；基本面沿用第一批不變）。
籌碼面走 v2 Worker `/chips?id=`（`loadChips` 逐股 lazy 取，非整批預抓；每股結果快取於 `CHIPS.data`），
四區塊（`trackChipsHtml` → `trackInstHtml/trackMarginHtml/trackFlowHtml/trackHoldHtml`）：

- **三大法人近日進出**：外資/投信/自營 × 最新日淨（張，買超紅賣超綠）＋連續買賣天數（|天數|≥3 加粗高亮）
  ＋近 5 日合計＋近 20 日迷你趨勢。
- **融資融券**：融資/融券餘額＋增減（增紅減綠）＋券資比（融券÷融資，軋空參考）＋近 20 日融資餘額趨勢。
- **借券賣出／當沖**：借券賣出餘額＋增減、當沖比（當沖量÷成交量）。
- **持股結構**：外資持股率＋區間 pp 變化；**千張大戶持股比＋週變化（標「T-1 週資料」）**——千張大戶為
  FinMind 付費層，Worker token 可取到（取不到則顯示 `big_note` 降級文字，其餘欄不受影響）。

**誠實原則**：純現況描述、各項標資料日、千張大戶標週資料、非投資建議。前端仍**絕不碰 FinMind token**。
詳端點規格見 taiwan-flow-live-v2 `PROJECT_SUMMARY.md`「快速接手（/chips）」。技術面（第三批）已於
2026-07-20 上線（見下）；三批（基本/籌碼/技術）至此完整。

## 個股追蹤 基本面 refine（4 點回饋，2026-07-20）

第一批基本面上線後的 4 點改進，全部走 v2 Worker `/fundamentals`（前端仍**絕不碰 FinMind token**）：

1. **每日新聞升級（消除「不在新聞池」死路）**：`trackNewsHtml(c,d)` 改讀 Worker 回的 `d.news`
   （媒體新聞 TaiwanStockNews＋業績事件墊底，保證 ≥3）。媒體新聞掛外連、**業績事件**（`n.event`）
   標「業績事件」badge 不外連。舊「不在新聞追蹤池」死路已移除；不再依賴本站 news.json 的 ~150 檔池。
2. **月營收柱狀每根標金額**：`trackBars` 每柱加 `.trk-bar-val`（億元，`barYi` 格式化；CSS `rotate(-55deg)`
   斜排避重疊，容器高 104px）。
3. **季財報加股利列**：`trackDividendRow(d.dividend)` 於 `trackFinHtml` 內呼叫，顯示現金/股票股利
   ＋除息日＋配發年度季別（資料 TaiwanStockDividend）。
4. **自選股顯示名稱**：`trackName` 優先用 Worker 回的 `name`（TaiwanStockInfo），chip 與詳情標題顯示
   「代號 名稱」；新增自選股後 fundamentals 載入即解析名稱重繪。

Worker 端 additive 擴充與 cache 升版（`fund:4:`）、新聞窗 5 日/保留名額等細節見 v2 `PROJECT_SUMMARY.md`。
**未解**：殖利率欄暫未做（需股價）；冷門股媒體新聞覆蓋率仍依 FinMind（靠業績事件墊底保證有內容）。

## 個股追蹤（第三批：技術面，2026-07-20）

選中股詳情框第三分頁「技術面」（`TRACK.view==='tech'`，沿用第二批分頁機制；基本面/籌碼面不變）。
技術面走 v2 Worker `/technical?id=`（`loadTech` 逐股 lazy 取，非整批預抓；每股結果快取於 `TECH.data`），
**前端絕不碰 FinMind token**，7 項指標全在 Worker 算好回傳，前端只渲染。渲染函式：`trackTechHtml`＋
`trackMaHtml/trackKdHtml/trackMacdHtml/trackRsiHtml/trackBollHtml/trackVolHtml/trackR52Html`。

**誠實原則（專案鐵律）**：分頁頂部固定免責卡「技術指標為現況描述、非買賣訊號，僅供參考」；狀態詞
（超買/超賣/黃金交叉/死亡交叉/黏合/多空排列…）＝描述指標數學狀態的中性詞（`.tst` 中性色，不用紅綠），
**不寫該買該賣、不做預測**。紅漲綠跌（`sgn`）僅用於「現價距離%」正負與距 52 週高低%。7 項＝均線
MA5/10/20/60＋距離%＋排列、KD(9,3,3)、MACD(12,26,9)、RSI(5,10)、布林(20,2 %b)、量能(5/20日均量比)、
距 52 週高/低%。資料日標註；資料不足指標顯示「—（資料不足）」。

詳端點規格見 v2 `PROJECT_SUMMARY.md`「快速接手（/technical）」。**續作**：回測技術指標對波動的有效性
（7b 一併，先驗證再宣稱，比照 postmkt 持股診斷）。

## 架構
```
build_news.py        ← 每日 pipeline：讀股票池 → 抓新聞 → 過濾 → 產 news.json
news_curation.py     ← 來源白名單過濾邏輯（純函式，附白名單/正規化對照）
index.html           ← dashboard 前端（讀 news.json）
news.json            ← 每日由 GitHub Actions 產出並 commit
.github/workflows/build-news.yml
```

## 資料流
1. 股票池：`build_pool_from_finmind()` 自建（FinMind `TaiwanStockInfo` 取名稱/產業＋
   近 3 個交易日投信/外資買賣超，排除 ETF）。原依賴 `taiwan-stock-radar` 的 `scan_app.csv`
   已於 2026-07-10 隨該 repo 刪除而改為自建（`--pool-csv` 參數仍可指定外部 CSV 覆蓋）。
2. 逐檔抓 `TaiwanStockNews`（單日單請求，含 550/hr 節流）；日期範圍為涵蓋近 N 個
   交易日（預設 3）的**日曆日**區間（含夾雜與尾隨的週末/假日），排程每天三班
   06:30/15:00/22:37 台北跑（含週末，2026-07-12 起；晚班 2026-07-14 由 22:30 改
   22:37 錯開 GitHub cron 壅塞），週六日發布的新聞也收得到。
3. `news_curation.curate_news` 白名單過濾：
   - source 正規化 → 核心白名單 → CMoney「股市爆料同學會」論壇次級過濾
   - Yahoo 跨來源標題去重 →（核心 0 篇時）fallback pool → 仍 0 則留空
4. 輸出 `news.json`，前端 `index.html` 讀取呈現。

## 首次設定（一次性）
1. **Secrets → Actions** 新增 `FINMIND_TOKEN`（FinMind API token）。
2. **Settings → Pages** 來源選 `Deploy from a branch` → `main` / `/ (root)`。
3. 到 **Actions → 每日建置台股新聞 → Run workflow** 手動跑一次產生 `news.json`。

## 手動更新
```bash
FINMIND_TOKEN=xxx python build_news.py --lookback 3            # 增量（預設）
FINMIND_TOKEN=xxx python build_news.py --lookback 5 --full     # 全窗重抓
python tests/test_incremental.py                               # 增量正確性（免 token/免網路）
```

## 增量抓取（2026-07 起）

這支排程每小時跑一次（Worker dispatch，一天 16 班），但 5 日視窗裡只有「今天」會變
——實測 724 則新聞中今天只佔 73 則（~10%）。原本每班都把 `池150 × 日曆日7~10`
＝1000~1500 個 `(檔,日)` 請求全部重打，逼出 `--hourly-budget 1400`、`timeout 90 分`
與 Throttle 睡到整點。

- **增量規則**：只重抓最近的 UTC 切片，其餘沿用既有 `news.json`。池 150 的請求數
  約 **1050 → 300**。
- **`coverage` 區塊**：`news.json` 新增 `coverage:{dates,codes}`，記錄本檔已涵蓋哪些
  `(日曆日切片 × 代號)`。「有涵蓋但 `stocks` 裡沒出現」＝該檔該日**確實沒新聞**；
  少了這個區塊就無法與「沒抓過」區分，只能全量。舊版 `news.json`（無 coverage）
  會被判定為不可用快取而自動走全量並補上。
- **切片 vs 台北日（關鍵）**：FinMind 單日切片是 **UTC 日**，台北 = UTC+8，所以
  UTC 切片 `s` 涵蓋台北 `s 08:00 ~ (s+1) 07:59` → **台北 D 日的新聞散落在切片 D-1 與 D**。
  要完整重抓 R 個台北日就得抓 **R+1** 個切片（`--refresh-days` 是台北日數，程式自己 +1）。
- **每日自我校正**：21:37 台北的備援 cron 班跑 `--full`。過濾規則變更、快取被寫壞、
  coverage 漂移都會在這一班被沖掉。
- **退回全量的情形**：`--full`、無/舊版快取、或視窗新增了「不在 refresh 範圍又沒抓過」
  的舊切片（通常是 `--lookback` 變大）。
- **守門**：`tests/test_incremental.py` 用假語料驗證「增量輸出 == 全量輸出」，含
  台北日跨切片、新進池個股全窗補抓、快取段與新抓段不重複三個情境（CI: `tests` workflow）。

## 快速接手（2026-07-12）

- **時區修正（2026-07-20）**：`build_news.py` 的 `news_calendar_days()`／`recent_trading_days()`
  原本以 **UTC** 判定「今天」，台北清晨 8 點前（UTC 仍是前一天）算出的今天會是昨天，導致清晨的
  班天生慢一天。新增 `taipei_today()` 統一改用台北時間（UTC+8）判定，`lookback_days` 語意不變。
  commit `b64c4ef`（taiwan-stock-news）。上游觸發端 taiwan-flow-live-v2 同日另修
  `dispatchNews`/`dispatchMorning` 失敗重試 1 次（見該 repo `PROJECT_SUMMARY.md`），解決
  dispatch 無聲失敗的問題；兩者為配套修正、不同檔案。
- 晨報「昨日資金流向」段（2026-07-18 第八期）：跨 repo 讀 taiwan-flow-live-v2
  `data/daysummary/latest.json`（`flowSumHtml()`，插在籌碼卡與美股段之間；讀不到/解析失敗
  整段隱藏不擋晨報，卡片標「資料日 YYYY-MM-DD」）。上游為該 repo `daysummary.yml`（平日
  14:05 台北產出，口徑同其收盤總結卡）。
- `index.html` 現有 3 個 tab：新聞、晨報（跨 repo 讀 taiwan-flow-live-v2 `data/morning.json`）、
  **摘要分析**（2026-07-12 新增）。摘要分析為前端直呼 Claude，框架與 postmkt 逐字同源
  （callClaude/mdToHtml/Opus 4.8-Sonnet 5 模型切換）；localStorage key
  `anthropic_key`/`insight_model` 與 postmkt、taiwan-flow-live-v2 同 origin 共用（設一次三站通用）。
- insightGatherContext 彙整：大盤財金焦點新聞（impact=market 去重前12）、個股新聞熱度前15、
  晨報籌碼（gap/法人/投信連買賣/主動ETF；MORNING 未載入會先 `await loadMorning()` 再判空略過）、
  隔夜美股（各族群前3）。SYS prompt 為「新聞×籌碼共振」語境。
- 個股外連＋雲端儲存（2026-07-12）：insight 渲染中個股代號自動變連結，外開 Yahoo 技術分析頁
  （`linkifyStocks(html, knownSet)`，三站逐字一致、改動需三站同步）。分析結果自動存
  **postmkt repo** `data/analyses/insight-news-YYYYMMDD.json`（當日陣列、單日上限10筆、
  保留近3日），寫入用 localStorage `gh_token`（GitHub Fine-grained PAT，三站同 origin 共用、
  未設靜默跳過）；tab 內「雲端歷史（近3日）」免 token 列本站檔、點擊展開（raw CDN 約 5 分快取）。
  PAT 建法與維護細節見 postmkt `README.md`。
- 晨報籌碼段的法人資料日（2026-07-31 已修）：`chipsHtml()` 原本沒有自帶日期，法人數字視覺上
  繼承上方的 `MORNING.generated_at`（建置時間），但數字實為前一交易日（晨報本質即彙整昨日籌碼）。
  已改標 payload 既有的 `chips.inst.date`（`index.html:470`），沿用同卡主動ETF基準日的慣例。

## 每日晨報（自架版，2026-09-03）

上游的 `daily-brief.html` / `daily-brief-card.json` 由「雲端排程 session 每晨產製並 push」，
那個 session 不會跟著 fork 過來。自架版改成**排程 Claude session + 程式強制版式**：

| 角色 | 負責 |
|---|---|
| 排程 cloud session（台北一~五 07:52） | 讀資料、網路搜尋、判讀與組稿 → 產出 content JSON |
| `brief_tools.py render` | 版式、存檔輪替（只留 7 期）、硬預算校驗（字數上下限、必填欄、行事曆日期） |
| `templates/brief_{head,tail}.html` | CSS 與 `</body>` 前的 iframe 自動高度 script（原樣保留） |

分工的理由：session 擅長判讀，不擅長每天重寫 516 行 HTML 又記得輪替存檔。
把後者交給程式後，硬預算是**強制**而不是寫在 prompt 裡祈禱——
`brief_tools.py render` 不符規範會 exit 1 且不寫檔。

### 字數為什麼要有下限

第一版的 schema 從頭到尾只寫「≤N 字」。2026-09-03 第 28 期與上游同日並排，
正文 **3,464 vs 5,064 漢字**，而 1,545 字的落差幾乎全部集中在生活區塊
（85 vs 1,630）——模型看到的全是上限，自然貼著上限以下寫，只用掉預算的 69%。

所以 `LIMITS` 改成 `(下限, 上限)` 兩端都擋。同批修掉的還有：

| 缺陷 | 現在由程式擋下 |
|---|---|
| 生活區塊寫成一行標題 | `life.note` 200~500 字，`life` 3~4 塊 |
| 三件事只有標題＋一句話，沒有判讀層、沒有來源 | `top3` 新增必填 `source`／`source_url`（須 `https://`），版式固定輸出編號＋`【判讀】`＋來源連結 |
| 「本週關鍵事件」飄到月中（第 28 期 7 則裡只有 2 則在本週） | 非空時至少 4 則的日期要真的落在本週（週一~週日），程式自己解析 `when` 裡的 M/D |
| 寫出「台股今日無除權息個股（morning.json exdiv 欄為空）」 | 任何欄位不得出現 json 檔名或「欄為空」；`week_events` 不得寫「無 X」非事件 |
| 開盤前定位把數個市場擠進同一列（6 列 vs 上游 12 列） | `positioning` 6~10 列，`fact` 4~60 **字元** |
| 超出總預算時只知道超標、不知道要削哪 | 失敗時印出各區塊漢字數 |

`positioning.fact` 是全篇唯一用字元數而非漢字數的欄位——它幾乎都是數字，
「46,164.72」的漢字數是 0，套漢字下限會把最正常的一列誤殺。

`tests/test_brief_tools.py` 把上面每一條都釘成 CI 會擋的失敗，
並額外驗「所有下限相加（1,400 字）遠低於總上限」——避免上下限互相鎖死到無解。

- `python3 brief_tools.py schema` — 印出 content JSON 的格式
- `python3 brief_tools.py check` — 校驗現有檔案（含判讀／來源連結是否齊備）
- `build_brief.py` + `build-brief.yml` — **備援**，直接呼叫 Anthropic API（約 $6/月），
  只能手動觸發。它沒有網路搜尋，所以 `week_events` 只能留空（留空是合法的）。
  版式與校驗共用 `brief_tools`，不再自帶 render 複本，兩條路徑的版式不會分岔；
  違規時會帶著違規清單重問一次模型再寫檔。
