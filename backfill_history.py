# -*- coding: utf-8 -*-
"""
大戶加碼篩選工具 — 歷史資料回補（選用）
從 TDCC 官網「集保戶股權分散表查詢」逐股抓取過去幾週的資料。
TDCC 開放資料只提供最新一週，想立刻比較 1~4 週變化就需要先回補。

用法：
  python backfill_history.py            # 回補最近 4 週（不含已有的快照）
  python backfill_history.py --weeks 2  # 只回補 2 週
  python backfill_history.py --test     # 偵錯：只查一檔並存下回應網頁

注意：
- 全市場約 2,000 檔，每檔一個請求（間隔 0.4 秒），每一週約需 15 分鐘。
- 可中斷，重新執行會從上次進度續抓。
- 請先執行過一次 update_data.py（需要 stock_info.json 與本週快照）。
"""
import argparse
import gzip
import http.cookiejar
import json
import ssl
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(BASE, "snapshots")
STOCK_INFO = os.path.join(BASE, "stock_info.json")
QRY_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SLEEP = 0.4  # 請求間隔（秒），請勿調太低以免被封鎖

cj = http.cookiejar.CookieJar()
try:
    urllib.request.urlopen(QRY_URL, timeout=10)
    _handlers = [urllib.request.HTTPCookieProcessor(cj)]
except Exception as _e:
    if "CERTIFICATE_VERIFY_FAILED" in str(_e):
        # 部分電腦驗不過 TDCC 憑證鏈 → 全程改用不驗證模式（僅公開資料）
        _ctx = ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = ssl.CERT_NONE
        _handlers = [urllib.request.HTTPSHandler(context=_ctx),
                     urllib.request.HTTPCookieProcessor(cj)]
        print("（憑證驗證失敗，改用不驗證模式）")
    else:
        _handlers = [urllib.request.HTTPCookieProcessor(cj)]
opener = urllib.request.build_opener(*_handlers)
opener.addheaders = [("User-Agent", UA),
                     ("Referer", QRY_URL)]


def fetch(url, data=None, timeout=30):
    body = urllib.parse.urlencode(data).encode() if data else None
    with opener.open(url, body, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_form(html):
    """解析查詢表單：所有 input（不限屬性順序）＋可查詢日期清單"""
    fields = {}
    for tag in re.findall(r"<input[^>]*>", html):
        nm = re.search(r'name=["\']([^"\']+)["\']', tag)
        if not nm:
            continue
        vm = re.search(r'value=["\']([^"\']*)["\']', tag)
        fields[nm.group(1)] = vm.group(1) if vm else ""
    dates = re.findall(r'<option[^>]*value=["\'](\d{8})["\']', html)
    if not dates:
        dates = re.findall(r"<option[^>]*>\s*(\d{8})\s*</option>", html)
    # select 欄位名稱（日期下拉選單），常見為 scaDate 或 scaDates
    sel = re.findall(r'<select[^>]*name=["\']([^"\']+)["\']', html)
    return fields, dates, sel


def parse_table(html):
    """解析查詢結果表格 → {level:int -> (people, units, pct)}，容忍巢狀標籤"""
    out = {}
    cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
             for c in re.findall(r"<td[^>]*>(.*?)</td>", html, re.S)]
    i = 0
    while i < len(cells):
        c = cells[i]
        if re.fullmatch(r"\d{1,2}", c) and 1 <= int(c) <= 17 and i + 4 < len(cells):
            try:
                lvl = int(c)
                people = int(cells[i + 2].replace(",", ""))
                units = int(cells[i + 3].replace(",", ""))
                pct = float(cells[i + 4].replace(",", "").replace("%", ""))
                out[lvl] = (people, units, pct)
                i += 5
                continue
            except ValueError:
                pass
        i += 1
    return out if len([k for k in out if 1 <= k <= 15]) == 15 else None


def query_one(fields, sel_names, date, stock_no):
    data = dict(fields)
    data.update({
        "sqlMethod": "StockNo",
        "stockNo": stock_no,
        "stockName": "",
        "method": "submit",
    })
    # 日期下拉選單：把頁面上所有 select 都填上目標日期（涵蓋 scaDate/scaDates 各種命名）
    data.setdefault("scaDate", date)
    data["scaDate"] = date
    for s in sel_names:
        data[s] = date
    html = fetch(QRY_URL, data)
    return parse_table(html), html


def refresh_form():
    html = fetch(QRY_URL)
    fields, dates, sel = parse_form(html)
    return fields, dates, sel, html


def existing_dates():
    if not os.path.isdir(SNAP_DIR):
        return set()
    return {re.search(r"(\d{8})", f).group(1)
            for f in os.listdir(SNAP_DIR)
            if re.fullmatch(r"tdcc_\d{8}\.csv\.gz", f)}


def save_debug(name, content):
    path = os.path.join(BASE, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [debug] 已存 {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=4, help="回補週數（預設 4）")
    ap.add_argument("--test", action="store_true", help="偵錯模式：只查一檔")
    args = ap.parse_args()

    print("連線 TDCC 查詢頁...")
    fields, dates, sel, page = refresh_form()
    if not dates:
        save_debug("debug_page.html", page)
        print("!! 無法從查詢頁取得可查詢日期，已存 debug_page.html")
        sys.exit(1)
    dates = sorted(set(dates), reverse=True)  # 新到舊

    if args.test:
        d = dates[1] if len(dates) > 1 else dates[0]
        print(f"偵錯：查詢 2330 @ {d}")
        print(f"  表單欄位: {list(fields.keys())}")
        print(f"  下拉選單: {sel}")
        save_debug("debug_page.html", page)
        table, html = query_one(fields, sel, d, "2330")
        save_debug("debug_response.html", html)
        if table:
            print("  解析成功！第15級:", table.get(15))
        else:
            print("  !! 解析失敗，請把 debug_page.html 與 debug_response.html 給 Claude 檢查")
        return

    if not os.path.exists(STOCK_INFO):
        print("!! 找不到 stock_info.json，請先執行 update_data.py")
        sys.exit(1)
    with open(STOCK_INFO, encoding="utf-8") as f:
        codes = sorted(json.load(f).keys())
    print(f"股票清單：{len(codes)} 檔")

    have = existing_dates()
    targets = [d for d in dates if d not in have][:args.weeks]
    if not targets:
        print("沒有需要回補的日期。")
        return
    print(f"預計回補：{', '.join(targets)}")

    os.makedirs(SNAP_DIR, exist_ok=True)
    for date in targets:
        part = os.path.join(SNAP_DIR, f"tdcc_{date}.csv.part")
        done = set()
        if os.path.exists(part):
            with open(part, encoding="utf-8") as f:
                done = {r.split(",")[1] for r in f if "," in r}
            print(f"[{date}] 續抓，已完成 {len(done)} 檔")
        fail = 0
        with open(part, "a", encoding="utf-8", newline="") as f:
            for n, code in enumerate(codes, 1):
                if code in done:
                    continue
                table = None
                nodata = False
                try:
                    table, html = query_one(fields, sel, date, code)
                    nodata = "查無" in html
                    if not table and not nodata:
                        # 換新表單（token 可能過期）再試一次
                        fields, _, sel, _ = refresh_form()
                        time.sleep(1)
                        table, html = query_one(fields, sel, date, code)
                        nodata = "查無" in html
                except Exception as e:
                    print(f"  {code} 連線錯誤：{e}（10 秒後重試）")
                    time.sleep(10)
                    try:
                        fields, _, sel, _ = refresh_form()
                        table, html = query_one(fields, sel, date, code)
                    except Exception:
                        table = None
                if table:
                    for lvl in sorted(table):
                        p, u, pc = table[lvl]
                        f.write(f"{date},{code},{lvl},{p},{u},{pc:.2f}\n")
                    f.flush()
                    fail = 0
                    # 從回應頁更新 token（若有）
                    nf, _, _ = parse_form(html)
                    if nf.get("SYNCHRONIZER_TOKEN"):
                        fields = nf
                elif nodata:
                    fail = 0  # 該檔該週真的沒資料（未上市/下市），跳過
                else:
                    fail += 1
                    if fail == 10:
                        save_debug("debug_response.html", html if 'html' in dir() else "")
                    if fail >= 20:
                        print("!! 連續 20 檔解析失敗，已存 debug_response.html，")
                        print("   請把該檔給 Claude 檢查；稍後重新執行可續抓。")
                        sys.exit(1)
                if n % 100 == 0:
                    print(f"  [{date}] 進度 {n}/{len(codes)}")
                time.sleep(SLEEP)
        # 完成 → 轉成 .csv.gz（加上表頭，與開放資料格式一致）
        gz = os.path.join(SNAP_DIR, f"tdcc_{date}.csv.gz")
        with open(part, encoding="utf-8") as src, \
                gzip.open(gz, "wt", encoding="utf-8") as dst:
            dst.write("資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n")
            dst.write(src.read())
        os.remove(part)
        print(f"[{date}] 完成 ✓")

    print("\n回補完成！請再執行一次 update_data.py 重新產生 data.js。")


if __name__ == "__main__":
    main()
