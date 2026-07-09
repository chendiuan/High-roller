# 放上 GitHub 並公開分享 — 步驟教學

完成後你會得到一個網址（如 `https://你的帳號.github.io/stock-whale/`），
朋友直接打開就能看，且每週六自動更新資料，完全不用手動維護。

## 一、建立倉庫

1. 到 [github.com](https://github.com) 註冊/登入
2. 右上角「+」→「New repository」
3. Repository name 輸入例如 `stock-whale`，選 **Public**，按「Create repository」

## 二、上傳檔案

1. 在新倉庫頁面點「uploading an existing file」連結
2. 把資料夾裡這些檔案/資料夾**全部拖進去**：
   - `dashboard.html`、`index.html`、`data.js`
   - `update_data.py`、`backfill_history.py`
   - `stock_info.json`、`README.md`、`.gitignore`
   - `snapshots/` 資料夾（整個拖入，歷史資料）
   - `prices/` 資料夾（若存在）
   - ※ 不用上傳：`run_update.bat`、`debug_*.html`、`上傳GitHub教學.md`
3. 按「Commit changes」

## 三、建立自動更新排程

（網頁上傳會忽略 `.github` 隱藏資料夾，所以手動建一次）

1. 倉庫頁面 →「Add file」→「Create new file」
2. 檔名欄位輸入：`.github/workflows/update.yml`（會自動變成資料夾結構）
3. 打開本機的 `.github\workflows\update.yml`，把內容全部複製貼上
4. 按「Commit changes」

## 四、開啟網頁（GitHub Pages）

1. 倉庫「Settings」→ 左側「Pages」
2. Source 選「**Deploy from a branch**」，Branch 選 `main`、`/(root)`，按 Save
3. 等 1~2 分鐘，頁面上方會顯示你的網址：
   `https://你的帳號.github.io/stock-whale/`

## 五、測試自動更新

1. 倉庫「Actions」頁籤 → 若有提示先按啟用
2. 左側點「每週更新資料」→ 右側「Run workflow」→ 綠色按鈕執行
3. 幾分鐘後顯示綠色勾勾 = 成功；之後每週六 13:00（台灣時間）自動執行

## 六、分享

把網址傳給朋友即可。手機也能開。

---

**疑難排解**
- Actions 執行失敗且錯誤在「下載 TDCC」：可能是 GitHub 主機的國外 IP
  被 TDCC 擋下。解法：改在自己電腦跑 `run_update.bat`，然後把更新後的
  `data.js`、`snapshots/`、`prices/` 重新拖曳上傳到倉庫即可更新網站。
- 網頁 404：Pages 需要 1~2 分鐘生效，稍等後重新整理。
