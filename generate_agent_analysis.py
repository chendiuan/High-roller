#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_agent_analysis.py
=================================
產生「股票分析」三個分頁（Market Researcher / Model Builder / Earnings Reviewer）
所需的靜態資料檔 agent_data.js，供 dashboard.html 讀取顯示。

概念參考自 Anthropic〈Claude for Financial Services〉一文的精神：
  - 把多個資料來源（大戶籌碼、股價、YouTube 研究影片）整合到單一畫面
  - 每個結論都附上可回頭查證的原始資料連結（Yahoo 行情、YouTube 影片）
  - 用三種角色分工，模擬市場研究員 / 建模助手 / 財報審閱員的工作內容

⚠️ 預設模式下本腳本「不會」對外連網抓取任何資料，也不會呼叫任何 AI API。
   純粹是用本地既有的 data.js（TDCC 大戶籌碼 + 股價）與 youtube_data.js
   （YouTube 影片標題/描述/摘要）做規則式整理，屬於靜態、離線產生的內容。
   結果僅供研究參考，非投資建議，使用前請自行查證原始來源。

用法：
    python generate_agent_analysis.py
    → 產生 agent_data.js，重新整理 dashboard.html 即可看到新分頁的內容。

    python generate_agent_analysis.py --ai
    → 額外對「加碼／減碼幅度最大」的前 AI_TOP_N 檔個股，改用本機 AI 推論伺服器
      （vLLM，OpenAI 相容 API）生成市場研究員分頁的敘述文字，取代規則式模板。
      這是選用功能，需要：
        1. 本機（或內網可達）已架好 vLLM 推論伺服器（見 AI_SERVER_SETUP.md）
        2. 設定環境變數 LOCAL_AI_API_KEY（伺服器的 --api-key）
        3. 設定環境變數 LOCAL_AI_BASE_URL（預設 http://127.0.0.1:8000/v1）
      呼叫失敗（伺服器沒開、逾時等）會自動退回原本的規則式敘述，不會中斷整個產生流程。
      **API key 只從環境變數讀取，絕不寫入 agent_data.js 或進 git repo。**
"""
import json
import os
import pathlib
import datetime
import urllib.request
import urllib.error

BASE = pathlib.Path(__file__).parent
THRESHOLD = "400"      # 對應 dashboard 預設的「大戶門檻」
DEFAULT_SHOWN = 20      # 分頁預設（未搜尋時）顯示筆數，其餘可透過搜尋找到
MAX_VIDEOS_PER_STOCK = 3
EARNINGS_KEYWORDS = ["財報", "法說", "營收", "EPS", "毛利", "展望", "財測",
                      "季報", "年報", "法人說明會", "營運展望", "財報季"]

# --- 選用的本機 AI 推論設定（只有加 --ai 才會用到）---
AI_BASE_URL = os.environ.get("LOCAL_AI_BASE_URL", "http://127.0.0.1:8000/v1")
AI_MODEL = os.environ.get("LOCAL_AI_MODEL", "qwen3.6-35b-a3b-fp8")
AI_TOP_N = int(os.environ.get("LOCAL_AI_TOP_N", "30"))  # 只對變化幅度最大的前 N 檔生成 AI 敘述，避免上千檔全跑太久
AI_TIMEOUT = 60


def call_local_ai(prompt):
    """呼叫本機 vLLM (OpenAI 相容) API，回傳生成文字；失敗時拋出例外由呼叫端接住。"""
    api_key = os.environ.get("LOCAL_AI_API_KEY")
    if not api_key:
        raise RuntimeError("未設定環境變數 LOCAL_AI_API_KEY，無法呼叫本機 AI 伺服器")
    body = json.dumps({
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 400,
        "temperature": 0.4,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{AI_BASE_URL}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"].strip()


def load_js_object(path):
    """讀取 window.XXX = {...}; 格式的 .js 檔，回傳解析後的 dict。"""
    text = path.read_text(encoding="utf-8")
    text = text[text.index("=") + 1:].strip()
    if text.endswith(";"):
        text = text[:-1]
    return json.loads(text)


def yahoo_url(code, market):
    return f"https://tw.stock.yahoo.com/quote/{code}{'.TWO' if market == '上櫃' else '.TW'}"


def fmt_d(s):
    return f"{s[4:6]}/{s[6:8]}"


def safe_round(v, n=2):
    return None if v is None else round(v, n)


def build_ai_prompt(name, code, industry, win_desc, r, related):
    lines = [
        f"你是台股market研究員，請用繁體中文寫一段約80~120字的籌碼動向分析，語氣專業、精簡，不要條列、不要標題，只要一段完整敘述。",
        f"個股：{name}（{code}），產業：{industry or '未分類'}。",
        f"比較區間：{win_desc}。",
        f"{THRESHOLD}張以上大戶持股比：{r['pa']:.2f}% → {r['pb']:.2f}%（{'+' if r['dp']>=0 else ''}{r['dp']:.2f}個百分點）。",
        f"同期持股張數變化：{'+' if r['du']>=0 else ''}{r['du']:,}張，大戶人數變化：{'+' if r['dc']>=0 else ''}{r['dc']}人。",
    ]
    if related:
        titles = "、".join(v.get("title", "") for v in related if v.get("title"))
        if titles:
            lines.append(f"近期相關YouTube財經影片討論：{titles}。")
    lines.append("請直接給出分析內容，並在合適處提醒這只是籌碼面觀察，並非投資建議。")
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ai", action="store_true",
                         help="對變化幅度最大的前 N 檔個股，改用本機 AI 推論伺服器生成市場研究員敘述")
    args = parser.parse_args()

    tdcc = load_js_object(BASE / "data.js")
    dates = tdcc["dates"]
    stocks = tdcc["stocks"]
    if len(dates) < 2:
        raise SystemExit("目前只有 1 週資料，尚無法計算大戶動向變化，請先累積至少 2 週資料。")

    ai, li = 0, len(dates) - 1  # 比較區間：最早一週 → 最新一週
    win_desc = f"{fmt_d(dates[ai])} → {fmt_d(dates[li])}"

    yt_path = BASE / "youtube_data.js"
    videos = []
    if yt_path.exists():
        yt = load_js_object(yt_path)
        videos = yt.get("recent_videos") or yt.get("new_videos") or []

    # ---------- 1. 計算所有個股在比較區間的大戶動向 ----------
    rows = []
    for code, st in stocks.items():
        t = (st.get("t") or {}).get(THRESHOLD)
        if not t:
            continue
        pa, pb = t["p"][ai], t["p"][li]
        if pa is None or pb is None:
            continue
        dp = round(pb - pa, 2)
        du = round((t["u"][li] - t["u"][ai]) / 1000)
        dc = t["c"][li] - t["c"][ai]
        rows.append({"code": code, "st": st, "pa": pa, "pb": pb, "dp": dp, "du": du, "dc": dc})

    # 產生「所有」有效資料的個股（供手動搜尋任一檔），預設依變化幅度排序，
    # 讓加碼/減碼最明顯的排在前面，其餘可透過分頁的搜尋框自行查詢。
    focus_rows = sorted(rows, key=lambda r: abs(r["dp"]), reverse=True)

    # ---------- 2. 準備 YouTube 影片可搜尋文字 ----------
    def video_text(v):
        a = v.get("analysis") or {}
        parts = [v.get("title", ""), v.get("description", ""), a.get("gist", "")]
        parts += a.get("key_points") or []
        parts += a.get("mentioned_topics") or []
        return " ".join(parts)

    video_index = [(v, video_text(v)) for v in videos]

    def find_videos_for(name):
        matches = [v for v, text in video_index if name and name in text]
        matches.sort(key=lambda v: v.get("published", ""), reverse=True)
        return matches[:MAX_VIDEOS_PER_STOCK]

    def video_brief(v):
        a = v.get("analysis") or {}
        return {
            "title": v.get("title"),
            "channel": v.get("channel_name"),
            "url": v.get("url"),
            "published": v.get("published"),
            "gist": a.get("gist"),
            "key_points": (a.get("key_points") or [])[:4],
        }

    # ---------- 3. 各角色分頁內容 ----------
    market_researcher, model_builder, earnings_reviewer = [], [], []

    # 依產業分組，供 Model Builder 做同業比較
    by_industry = {}
    for code, st in stocks.items():
        t = (st.get("t") or {}).get(THRESHOLD)
        if not t or t["p"][li] is None:
            continue
        by_industry.setdefault(st.get("i") or "其他", []).append(
            {"code": code, "name": st.get("n"), "pct": t["p"][li],
             "dp": round(t["p"][li] - (t["p"][ai] if t["p"][ai] is not None else t["p"][li]), 2)}
        )

    ai_success_count, ai_fail_count = 0, 0
    for idx, r in enumerate(focus_rows):
        code, st = r["code"], r["st"]
        name, industry, market = st.get("n"), st.get("i"), st.get("m")
        direction = "加碼" if r["dp"] >= 0 else "減碼"
        related = [video_brief(v) for v in find_videos_for(name)]
        yurl = yahoo_url(code, market)

        # --- Market Researcher ---
        narrative = (
            f"{name}（{code}）在比較區間 {win_desc}，{THRESHOLD}張以上大戶持股比由 "
            f"{r['pa']:.2f}% 變動至 {r['pb']:.2f}%（{'+' if r['dp']>=0 else ''}{r['dp']:.2f}pp，"
            f"研判為{direction}），持股張數變化 {'+' if r['du']>=0 else ''}{r['du']:,} 張，"
            f"大戶人數變化 {'+' if r['dc']>=0 else ''}{r['dc']}人。"
        )
        if related:
            narrative += f" 另在 {len(related)} 支近期 YouTube 財經影片中被提及，可交叉參考影片論點與大戶籌碼方向是否一致。"
        else:
            narrative += " 目前追蹤的 YouTube 頻道近期未提及此股，建議自行搜尋最新新聞或法說會資訊佐證。"

        narrative_source = "rule"
        if args.ai and idx < AI_TOP_N:
            try:
                ai_text = call_local_ai(build_ai_prompt(name, code, industry, win_desc, r, related))
                if ai_text:
                    narrative = ai_text
                    narrative_source = "ai"
                    ai_success_count += 1
            except Exception as e:
                ai_fail_count += 1
                print(f"  [警告] {name}（{code}）AI敘述生成失敗，改用規則式敘述：{e}")

        market_researcher.append({
            "code": code, "name": name, "industry": industry, "market": market,
            "direction": direction,
            "narrative_source": narrative_source,
            "holder_trend": {"threshold": THRESHOLD, "window": win_desc,
                              "from_pct": r["pa"], "to_pct": r["pb"], "pp_change": r["dp"],
                              "unit_change": r["du"], "people_change": r["dc"]},
            "price": {"latest": st.get("pr"), "series": st.get("px"), "dates": dates},
            "related_videos": related,
            "source_links": [
                {"label": "Yahoo 股市行情", "url": yurl},
                {"label": "TDCC 集保戶股權分散表（原始快照見 snapshots/）", "url": "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"},
            ],
            "narrative": narrative,
        })

        # --- Model Builder：同業比較表 ---
        full_peers = sorted(by_industry.get(industry, []), key=lambda p: p["pct"], reverse=True)
        rank = next((i + 1 for i, p in enumerate(full_peers) if p["code"] == code), None)
        peers = [dict(p, is_focus=(p["code"] == code)) for p in full_peers[:5]]
        if rank is not None and rank > 5:
            own = next(p for p in full_peers if p["code"] == code)
            peers.append(dict(own, is_focus=True))
        px = st.get("px") or []
        price_change_pct = None
        if len(px) > li and px[ai] not in (None, 0) and px[li] is not None:
            price_change_pct = safe_round((px[li] - px[ai]) / px[ai] * 100, 2)
        if price_change_pct is None:
            alignment = "資料不足，無法判斷籌碼與股價是否同向"
        elif (r["dp"] >= 0) == (price_change_pct >= 0):
            alignment = "同向（大戶籌碼變化與股價漲跌方向一致）"
        else:
            alignment = "背離（大戶籌碼變化與股價漲跌方向不同，可留意後續是否收斂）"

        model_builder.append({
            "code": code, "name": name, "industry": industry, "market": market,
            "peer_rank": {"rank": rank, "of": len(full_peers), "threshold": THRESHOLD},
            "peers": peers,
            "price_change_pct": price_change_pct,
            "holder_pp_change": r["dp"],
            "alignment": alignment,
            "source_links": [{"label": "Yahoo 股市行情", "url": yurl}],
            "note": "此為以大戶持股比＋股價區間資料建立的簡易同業比較框架，非完整財務模型（未含 EPS、營收、估值倍數），"
                    "可作為進一步建模的起點。",
        })

        # --- Earnings Reviewer ---
        earnings_videos = []
        for v, text in video_index:
            if name and name in text and any(k in text for k in EARNINGS_KEYWORDS):
                earnings_videos.append(video_brief(v))
        earnings_videos = earnings_videos[:MAX_VIDEOS_PER_STOCK]
        earnings_reviewer.append({
            "code": code, "name": name, "market": market,
            "earnings_related_videos": earnings_videos,
            "has_local_signal": bool(earnings_videos),
            "checklist": [
                "至公開資訊觀測站（MOPS）查詢最新季報／年報與重大訊息",
                "確認近期是否有法說會，比對法人預估與公司財測",
                "留意大戶籌碼變化（見上方 Market Researcher）是否與財報發布時間點吻合",
            ],
            "source_links": [
                {"label": "公開資訊觀測站 MOPS", "url": "https://mops.twse.com.tw/mops/web/index"},
                {"label": "Yahoo 股市行情", "url": yurl},
            ],
        })

    disclaimer = ("本頁三個分頁由本機既有資料（TDCC 大戶籌碼、股價、YouTube 影片摘要）整理產生，"
                  "未即時連網查證，僅供研究參考，非投資建議。")
    if args.ai:
        disclaimer += (f"其中市場研究員分頁前 {AI_TOP_N} 檔（依籌碼變化幅度排序）的敘述文字"
                        "由本機自架 AI 推論伺服器生成，其餘與另兩個分頁維持規則式整理，"
                        "AI 生成內容同樣僅供研究參考，非投資建議，請自行查證。")

    out = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "window_desc": win_desc,
        "threshold": THRESHOLD,
        "focus_stock_count": len(focus_rows),
        "default_shown": DEFAULT_SHOWN,
        "ai_enabled": args.ai,
        "disclaimer": disclaimer,
        "market_researcher": market_researcher,
        "model_builder": model_builder,
        "earnings_reviewer": earnings_reviewer,
    }

    out_path = BASE / "agent_data.js"
    out_path.write_text("window.AGENT_DATA=" + json.dumps(out, ensure_ascii=False) + ";", encoding="utf-8")
    print(f"已產生 {out_path.name}：涵蓋 {len(focus_rows)} 檔個股（可於分頁內搜尋任一檔），比較區間 {win_desc}")
    if args.ai:
        print(f"AI敘述生成：成功 {ai_success_count} 檔，失敗 {ai_fail_count} 檔（失敗已自動退回規則式敘述）")


if __name__ == "__main__":
    main()
