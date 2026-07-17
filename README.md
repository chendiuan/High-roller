# 大戶加碼篩選工具

追蹤台股「大戶持股比例」週變化，找出大戶加碼（或減碼）的股票。
資料來源：TDCC 集保戶股權分散表（每週五資料，約週六公布）。

## 快速開始

1. 安裝 [Python 3](https://www.python.org/downloads/)（安裝時勾選 *Add to PATH*）
2. 雙擊 `run_update.bat`（或執行 `python update_data.py`）
   → 下載本週資料、產生 `data.js`
3. 用瀏覽器開啟 `dashboard.html`

> 第一次執行只有一週資料，還算不出「加碼變化」。兩個選擇：
> - **立即回補歷史**：執行 `python backfill_history.py`（預設回補 4 週，
>   從 TDCC 官網逐股抓取，每週約 12~15 分鐘），完成後再跑一次 `update_data.py`
> - **每週累積**：之後每週執行一次 `run_update.bat`，資料會越疊越多

## 儀表板功能

- 大戶門檻：200 / 400 / 600 / 800 / 1000 張以上
- 比較區間：1 ~ 4 週
- 加碼 / 減碼排行，可依「持股比增減(pp)」或「張數變化」排序
- 市場（上市/上櫃）、持股比下限、代號/名稱搜尋
- 點擊個股展開：各週持股比走勢圖與明細，可連到 Yahoo 行情

## 檔案說明

| 檔案 | 用途 |
|---|---|
| `update_data.py` | 每週執行：下載最新股權分散表 → 產生 `data.js` |
| `backfill_history.py` | 選用：回補過去幾週歷史（TDCC 官網逐股查詢） |
| `dashboard.html` | 儀表板（離線可用，讀取 `data.js`） |
| `snapshots/` | 每週原始資料快照（`tdcc_YYYYMMDD.csv.gz`） |
| `stock_info.json` | 股票代號↔名稱/產業對照（FinMind，快取 30 天） |

## YouTube 每日待看清單

此專案也加入了 YouTuber 新影片追蹤功能，適合每天固定檢查投資、財經或研究頻道的新上傳影片。

1. 編輯 `youtube_channels.json`
   - 已預設追蹤 `@Gooaye`、`@oldwangstock`、`@yutinghaofinance`
   - 可自行新增或停用頻道
   - `url` 可填 `https://www.youtube.com/@handle` 或 `https://www.youtube.com/channel/UC...`
2. 手動執行：雙擊 `run_youtube_watchlist.bat`
   - 會偵測新影片
   - 產生 `youtube_inbox.html`
   - 產生每日筆記 `youtube_reports/youtube_new_videos_YYYYMMDD.md`
3. 安裝每日排程：用 PowerShell 執行

```powershell
powershell -ExecutionPolicy Bypass -File .\install_youtube_schedule.ps1
```

預設排程時間讀取 `youtube_channels.json` 的 `schedule_time`。若想改成每天 07:45：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_youtube_schedule.ps1 -Time 07:45
```

若希望偵測到新影片時自動開啟待看清單，將 `youtube_channels.json` 的 `open_new_videos` 改成 `true`。

### 在 GitHub Actions 排程

專案已包含 `.github/workflows/youtube-watchlist.yml`，預設每天台灣時間 06:00 執行。

使用方式：

1. 將 `youtube_channels.json` 裡要追蹤的頻道設為 `enabled: true`
2. Commit 並 push 到 GitHub
3. 到 GitHub repo 的 **Actions** 頁面啟用 workflow
4. 可用 **Run workflow** 手動測試一次

GitHub Actions 會自動提交：

- `youtube_seen.json`：已偵測過的影片，避免每天重複通知
- `youtube_inbox.html`：最新待看清單頁面
- `youtube_data.js`：待看清單資料
- `youtube_reports/`：每日 Markdown 筆記
- `youtube_transcripts/`：可取得字幕時保留逐字稿快取

### 自動逐字稿與重點摘要

YouTube 排程會優先嘗試抓影片字幕/自動字幕：

1. 有字幕且 GitHub Secret 設定 `OPENAI_API_KEY`：使用 OpenAI 產生逐字稿摘要、時間軸、主題與查證重點
2. 有字幕但沒有 `OPENAI_API_KEY`：使用逐字稿做規則式重點整理
3. 沒有字幕：退回標題、描述與發布時間整理

到 GitHub repo 的 **Settings → Secrets and variables → Actions → New repository secret** 新增：

```text
OPENAI_API_KEY
```

可選：在 **Variables** 新增 `OPENAI_MODEL`，預設為 `gpt-4.1-mini`。

## 股票分析（市場研究員 / 建模助手 / 財報審閱）

儀表板新增三個分頁，概念參考自 Anthropic〈Claude for Financial Services〉——把多個資料來源整合到單一畫面、每個結論都附上可回頭查證的原始連結。三個分頁模擬三種分工角色，皆由本機既有資料（大戶籌碼、股價、YouTube 影片摘要）規則式整理產生，**不連網、不呼叫任何 AI API**：

- 🔎 **市場研究員**：針對大戶加碼／減碼前 10 名個股，整合籌碼變化、股價與相關 YouTube 影片討論
- 📐 **建模助手**：以大戶持股比建立同業排名比較表，並標示籌碼與股價是否同向
- 📄 **財報審閱**：篩出財報／法說相關的 YouTube 討論，沒有本地資料的則列出人工查證清單（含 MOPS 連結）

執行方式：

```
python generate_agent_analysis.py
```

會產生 `agent_data.js`，重新整理 `dashboard.html` 即可看到內容。建議在每次跑完 `update_data.py` 之後接著執行，讓分析對應最新一週的大戶資料。

> 這三個分頁是規則式整理，非 AI 生成的投研報告，也不是完整財務模型或投資建議，僅供作為進一步研究的起點。

### 選用：用本機 AI 推論伺服器生成市場研究員敘述

如果自己有架設本機 AI 推論伺服器（OpenAI 相容 API，例如 vLLM，見下方「本機 AI 推論伺服器」），可以加 `--ai` 讓市場研究員分頁中「籌碼變化幅度最大」的前 N 檔（預設 30，可用 `LOCAL_AI_TOP_N` 調整）改用 AI 生成敘述文字，取代規則式模板；其餘個股與另外兩個分頁維持規則式整理不變。

```bash
export LOCAL_AI_API_KEY=你的伺服器API key    # 只在本機環境變數設定，絕不要寫進任何檔案或commit
export LOCAL_AI_BASE_URL=http://127.0.0.1:8000/v1   # 預設值，依實際伺服器位置調整
python generate_agent_analysis.py --ai
```

AI 呼叫失敗（伺服器沒開、逾時等）會自動退回規則式敘述，不會中斷整個產生流程。dashboard 上這些個股會多一個「✨ AI生成」標記。

## 本機 AI 推論伺服器

`AI_SERVER_SETUP.md` 記錄了如何在有 NVIDIA GPU 的機器上，從零架設一台跑開源 LLM 的本機推論伺服器（vLLM + OpenAI 相容 API），供上面「用本機 AI 推論伺服器生成市場研究員敘述」使用。這台伺服器只綁定 `127.0.0.1`，不對外開放，API key 存放在伺服器機器本機（`/etc/vllm-server.env`），不會出現在這個 repo 裡。

## 計算方式

TDCC 將每檔股票持股人依張數分 15 級。「400 張以上大戶持股比」
= 第 12~15 級（400,001 股以上）的「占集保庫存數比例%」加總。
加碼幅度 = 本週持股比 − N 週前持股比（百分點, pp）。

## 注意事項

- 排除 ETF、權證、債券，僅保留上市/上櫃 4 碼股票
- TDCC 官網僅保留約一年歷史；回補腳本有限流保護，中斷可續抓
- 本工具僅整理公開資料，非投資建議
