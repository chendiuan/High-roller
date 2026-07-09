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

## 計算方式

TDCC 將每檔股票持股人依張數分 15 級。「400 張以上大戶持股比」
= 第 12~15 級（400,001 股以上）的「占集保庫存數比例%」加總。
加碼幅度 = 本週持股比 − N 週前持股比（百分點, pp）。

## 注意事項

- 排除 ETF、權證、債券，僅保留上市/上櫃 4 碼股票
- TDCC 官網僅保留約一年歷史；回補腳本有限流保護，中斷可續抓
- 本工具僅整理公開資料，非投資建議
