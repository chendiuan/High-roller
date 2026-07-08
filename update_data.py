# -*- coding: utf-8 -*-
"""
大戶加碼篩選工具 — 每週資料更新腳本
資料來源：TDCC 集保戶股權分散表（開放資料，每週五資料、約週六更新）
用法：python update_data.py
純標準函式庫，無需安裝任何套件（需 Python 3.8+）。
"""
import csv
import gzip
import io
import json
import os
import re
import sys
import time
import ssl
import urllib.request
from datetime import date as _date, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(BASE, "snapshots")
STOCK_INFO = os.path.join(BASE, "stock_info.json")
DATA_JS = os.path.join(BASE, "data.js")

TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
FINMIND_INFO_URL = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo"
TWSE_PRICE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL"
TPEX_PRICE_URL = ("https://www.tpex.org.tw/openapi/v1/"
                  "tpex_mainboard_daily_close_quotes")
TWSE_HIST_URL = ("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
                 "?date={d}&type=ALLBUT0999&response=json")
TPEX_HIST_URL = TPEX_PRICE_URL + "?l=zh-tw&d={d}"
PRICE_DIR = os.path.join(BASE, "prices")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# 持股分級 → 張數門檻（該級距以上合計）
# 11:200-400張 12:400-600 13:600-800 14:800-1000 15:1000張以上
THRESHOLDS = {"200": 11, "400": 12, "600": 13, "800": 14, "1000": 15}


def http_get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise
        # 部分電腦驗不過 TDCC 的憑證鏈 → 改用不驗證模式重試
        # （僅下載公開資料，風險可接受）
        print("  （憑證驗證失敗，改用不驗證模式重試）")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read()


def _looks_complete(text):
    """完整檔案一定包含台積電/鴻海等一般股票；截斷檔會缺。
    注意代號欄可能補空格（如 "2330  "），且須避免誤配到人數/股數欄位。"""
    return (re.search(r"^\d{8},2330\s*,", text, re.M) is not None
            and re.search(r"^\d{8},2317\s*,", text, re.M) is not None)


def check_existing_snapshots():
    """把先前下載不完整的快照改名成 .bad，避免污染結果"""
    if not os.path.isdir(SNAP_DIR):
        return
    for f in sorted(os.listdir(SNAP_DIR)):
        if not re.fullmatch(r"tdcc_\d{8}\.csv\.gz", f):
            continue
        path = os.path.join(SNAP_DIR, f)
        try:
            with gzip.open(path, "rt", encoding="utf-8") as g:
                ok = _looks_complete(g.read())
        except Exception:
            ok = False
        if not ok:
            os.replace(path, path + ".bad")
            print(f"!! 快照 {f} 不完整（缺一般股票），已改名為 .bad")


def download_snapshot():
    """下載本週股權分散表，存為 snapshots/tdcc_YYYYMMDD.csv.gz（含完整性檢查）"""
    text = None
    for attempt in range(1, 4):
        print(f"下載 TDCC 股權分散表（約 30~60 MB，請稍候）... (第 {attempt} 次)")
        try:
            raw = http_get(TDCC_URL, timeout=300)
        except Exception as e:
            print(f"  !! 下載失敗：{e}")
            time.sleep(5)
            continue
        t = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
        first = t.splitlines()[1] if "\n" in t else ""
        m = re.match(r"(\d{8}),", first)
        if m and _looks_complete(t):
            text, date = t, m.group(1)
            break
        print(f"  !! 內容不完整或格式不符（{len(t)//1024} KB），重試...")
        time.sleep(5)
    if text is None:
        print("!! 連續 3 次下載都不完整，請稍後再試。")
        sys.exit(1)
    os.makedirs(SNAP_DIR, exist_ok=True)
    path = os.path.join(SNAP_DIR, f"tdcc_{date}.csv.gz")
    if os.path.exists(path):
        print(f"本週資料 {date} 已存在，略過。")
        return date
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"已儲存快照：{date}（{len(text)//1024} KB）")
    return date


def load_stock_info():
    """取得股票代號→名稱/產業/市場對照（FinMind 免費 API，快取 30 天）"""
    if os.path.exists(STOCK_INFO):
        age_days = (time.time() - os.path.getmtime(STOCK_INFO)) / 86400
        if age_days < 30:
            with open(STOCK_INFO, encoding="utf-8") as f:
                return json.load(f)
    print("更新股票基本資料（FinMind TaiwanStockInfo）...")
    try:
        data = json.loads(http_get(FINMIND_INFO_URL).decode("utf-8"))
        rows = data.get("data", [])
    except Exception as e:
        print(f"!! 無法取得股票名稱對照（{e}），將只顯示代號。")
        rows = []
    info = {}
    for r in rows:
        sid = r.get("stock_id", "")
        typ = r.get("type", "")
        ind = r.get("industry_category", "")
        # 僅保留上市/上櫃、4 碼純數字、排除 ETF/指數
        if typ not in ("twse", "tpex"):
            continue
        if not re.fullmatch(r"\d{4}", sid) or sid.startswith("00"):
            continue
        if "ETF" in ind or "Index" in ind or "大盤" in ind:
            continue
        # 同一檔可能多列（不同產業分類），保留第一個非「其他」者
        if sid in info and info[sid]["i"] not in ("其他", ""):
            continue
        info[sid] = {"n": r.get("stock_name", ""), "i": ind,
                     "m": "上市" if typ == "twse" else "上櫃"}
    if info:
        with open(STOCK_INFO, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
        print(f"股票對照共 {len(info)} 檔。")
    return info


def _to_price(v):
    try:
        p = float(str(v).replace(",", ""))
        return p if p > 0 else None
    except (ValueError, TypeError):
        return None


def fetch_prices():
    """抓上市/上櫃最近收盤價（官方 OpenAPI），失敗不影響主流程"""
    prices = {}
    print("更新收盤價...")
    try:
        for r in json.loads(http_get(TWSE_PRICE_URL, timeout=60)):
            p = _to_price(r.get("ClosingPrice"))
            if r.get("Code") and p:
                prices[r["Code"]] = p
    except Exception as e:
        print(f"  !! 上市收盤價取得失敗（{e}）")
    try:
        for r in json.loads(http_get(TPEX_PRICE_URL, timeout=60)):
            code = r.get("SecuritiesCompanyCode") or r.get("Code")
            p = _to_price(r.get("Close") or r.get("ClosingPrice"))
            if code and p:
                prices[code] = p
    except Exception as e:
        print(f"  !! 上櫃收盤價取得失敗（{e}）")
    print(f"  收盤價共 {len(prices)} 檔")
    return prices


def _hist_prices_one(date):
    """抓某一交易日全市場收盤價 → {code: price}"""
    out = {}
    # 上市：MI_INDEX（收盤行情表：代號=欄0，收盤價=欄8）
    try:
        j = json.loads(http_get(TWSE_HIST_URL.format(d=date), timeout=120))
        tables = j.get("tables") or []
        for tb in tables:
            if "每日收盤行情" in (tb.get("title") or ""):
                for row in tb.get("data") or []:
                    if len(row) > 8:
                        p = _to_price(row[8])
                        if p:
                            out[str(row[0]).strip()] = p
    except Exception as e:
        print(f"  !! 上市 {date} 收盤價失敗：{e}")
    # 上櫃：OpenAPI（民國年日期）
    try:
        roc = f"{int(date[:4])-1911}/{date[4:6]}/{date[6:]}"
        rows = json.loads(http_get(TPEX_HIST_URL.format(d=roc), timeout=120))
        for r in rows:
            code = (r.get("SecuritiesCompanyCode") or "").strip()
            p = _to_price(r.get("Close"))
            if code and p:
                out[code] = p
    except Exception as e:
        print(f"  !! 上櫃 {date} 收盤價失敗：{e}")
    return out


def fetch_hist_prices(dates):
    """各快照日期的全市場收盤價（逐日快取到 prices/）→ {date: {code: price}}"""
    os.makedirs(PRICE_DIR, exist_ok=True)
    result = {}
    for d in dates:
        cache = os.path.join(PRICE_DIR, f"px_{d}.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                result[d] = json.load(f)
            continue
        print(f"抓取 {d} 收盤價...")
        px = _hist_prices_one(d)
        print(f"  {d}：{len(px)} 檔")
        if px:
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(px, f)
            result[d] = px
        time.sleep(3)  # 官網禮貌間隔
    return result


def _week_days(d):
    """快照日（通常週五）所屬週的所有交易日 YYYYMMDD（週一~快照日）"""
    dt = _date(int(d[:4]), int(d[4:6]), int(d[6:]))
    mon = dt - timedelta(days=dt.weekday())
    return [(mon + timedelta(days=i)).strftime("%Y%m%d")
            for i in range((dt - mon).days + 1)]


def _daily_hl(day):
    """某交易日全市場盤中最高/最低 → {code: [low, high]}（休市日回傳空）"""
    out = {}
    try:  # 上市：最高=欄6、最低=欄7
        j = json.loads(http_get(TWSE_HIST_URL.format(d=day), timeout=120))
        for tb in j.get("tables") or []:
            if "每日收盤行情" in (tb.get("title") or ""):
                for row in tb.get("data") or []:
                    if len(row) > 8:
                        hi, lo = _to_price(row[6]), _to_price(row[7])
                        if hi and lo:
                            out[str(row[0]).strip()] = [lo, hi]
    except Exception as e:
        print(f"  !! 上市 {day} 高低價失敗：{e}")
    try:  # 上櫃
        roc = f"{int(day[:4])-1911}/{day[4:6]}/{day[6:]}"
        for r in json.loads(http_get(TPEX_HIST_URL.format(d=roc), timeout=120)):
            code = (r.get("SecuritiesCompanyCode") or "").strip()
            hi, lo = _to_price(r.get("High")), _to_price(r.get("Low"))
            if code and hi and lo:
                out[code] = [lo, hi]
    except Exception as e:
        print(f"  !! 上櫃 {day} 高低價失敗：{e}")
    return out


def fetch_week_hl(dates):
    """各快照週的盤中高低價區間（逐週快取）→ {date: {code: [low, high]}}"""
    os.makedirs(PRICE_DIR, exist_ok=True)
    result = {}
    for d in dates:
        cache = os.path.join(PRICE_DIR, f"wk_{d}.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                result[d] = json.load(f)
            continue
        print(f"抓取 {d} 當週高低價...")
        agg = {}
        for day in _week_days(d):
            hl = _daily_hl(day)
            if not hl:
                continue  # 休市日
            for c, (lo, hi) in hl.items():
                if c in agg:
                    agg[c][0] = min(agg[c][0], lo)
                    agg[c][1] = max(agg[c][1], hi)
                else:
                    agg[c] = [lo, hi]
            time.sleep(3)  # 官網禮貌間隔
        print(f"  {d}：{len(agg)} 檔")
        if agg:
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(agg, f)
            result[d] = agg
    return result


def read_snapshot(path):
    """讀取一份快照 → {code: {level:int -> (people, units, pct)}}"""
    out = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if len(row) < 6:
                continue
            _, code, lvl, people, units, pct = row[:6]
            code = code.strip()  # 代號欄可能補空格（如 "2330  "）
            try:
                lvl = int(lvl)
                rec = (int(people), int(units), float(pct))
            except ValueError:
                continue
            out.setdefault(code, {})[lvl] = rec
    return out


def build_dataset(info, prices=None, hist_px=None, week_hl=None):
    """彙整所有快照 → data.js"""
    files = sorted(
        f for f in os.listdir(SNAP_DIR)
        if re.fullmatch(r"tdcc_\d{8}\.csv\.gz", f)
    )
    if not files:
        print("!! 沒有任何快照。")
        sys.exit(1)
    dates = [re.search(r"(\d{8})", f).group(1) for f in files]
    print(f"彙整 {len(dates)} 週快照：{', '.join(dates)}")

    # stocks[code] = {n,i,m, t: {門檻: {p:[pct...], u:[units...], c:[people...]}}}
    stocks = {}
    for di, fname in enumerate(files):
        snap = read_snapshot(os.path.join(SNAP_DIR, fname))
        for code, levels in snap.items():
            if info and code not in info:
                continue  # 排除 ETF、債券、權證等
            if not info and not re.fullmatch(r"\d{4}", code):
                continue
            st = stocks.setdefault(code, {
                "n": info.get(code, {}).get("n", ""),
                "i": info.get(code, {}).get("i", ""),
                "m": info.get(code, {}).get("m", ""),
                "pr": (prices or {}).get(code),
                "px": [(hist_px or {}).get(d, {}).get(code)
                       for d in dates],
                "hl": [(week_hl or {}).get(d, {}).get(code)
                       for d in dates],
                "t": {k: {"p": [None] * len(dates),
                          "u": [None] * len(dates),
                          "c": [None] * len(dates)} for k in THRESHOLDS},
            })
            for key, min_lvl in THRESHOLDS.items():
                pct = units = people = 0
                for lvl in range(min_lvl, 16):
                    if lvl in levels:
                        c, u, p = levels[lvl]
                        people += c
                        units += u
                        pct += p
                st["t"][key]["p"][di] = round(pct, 2)
                st["t"][key]["u"][di] = units
                st["t"][key]["c"][di] = people

    payload = {"dates": dates, "stocks": stocks,
               "generated": time.strftime("%Y-%m-%d %H:%M")}
    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write("window.TDCC_DATA=")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";")
    size_mb = os.path.getsize(DATA_JS) / 1e6
    print(f"已產生 data.js（{size_mb:.1f} MB，{len(stocks)} 檔股票）")
    return payload


def preview(payload):
    """終端機快速預覽：400張以上、最近 vs 前一週加碼排行前 10"""
    dates = payload["dates"]
    if len(dates) < 2:
        print("\n目前只有一週資料，尚無法計算加碼變化。")
        print("→ 執行 backfill_history.py 回補歷史，或下週再執行本腳本累積資料。")
        return
    a, b = -2, -1
    rows = []
    for code, st in payload["stocks"].items():
        p = st["t"]["400"]["p"]
        u = st["t"]["400"]["u"]
        if p[a] is None or p[b] is None:
            continue
        rows.append((p[b] - p[a], code, st["n"], p[a], p[b],
                     (u[b] - u[a]) / 1000.0))
    rows.sort(reverse=True)
    print(f"\n== 400張以上大戶加碼 前10（{dates[a]} → {dates[b]}）==")
    for d, code, name, pa, pb, du in rows[:10]:
        print(f"{code} {name:<8} {pa:6.2f}% → {pb:6.2f}%  ({d:+.2f}pp, {du:+,.0f}張)")


if __name__ == "__main__":
    check_existing_snapshots()
    download_snapshot()
    stock_info = load_stock_info()
    px = fetch_prices()
    snap_dates = sorted(re.search(r"(\d{8})", f).group(1)
                        for f in os.listdir(SNAP_DIR)
                        if re.fullmatch(r"tdcc_\d{8}\.csv\.gz", f))
    hist_px = fetch_hist_prices(snap_dates)
    week_hl = fetch_week_hl(snap_dates)
    data = build_dataset(stock_info, px, hist_px, week_hl)
    preview(data)
    print("\n完成！用瀏覽器開啟 dashboard.html 查看結果。")
