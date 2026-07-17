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
      （vLLM，OpenAI 相容 API）生成三個分頁的敘述文字，取代規則式模板：
        - 市場研究員：籌碼動向敘述
        - 建模助手：同業比較評述
        - 財報審閱：有本地 YouTube 財報／法說討論的股票，AI 統整影片重點；
          沒有本地影片資料的股票，AI 改用已知的籌碼資料生成量身查證提醒
          （prompt 明確禁止捏造財報數字／營收／EPS，不會產生沒有根據的財務內容）。
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


# 常見簡體字殘留保底替換（LLM 偶爾會混用，prompt 指示不保證 100% 有效）
# 只挑簡體專用、在繁體中文裡不會有其他合法用法的字，避免誤判（例如「后」在繁體
# 裡可能是「皇后」的后，就不列入，只在明確無歧義時才替換）。
_SIMP_TO_TRAD = {
    "构": "構", "证": "證", "让": "讓", "问": "問", "题": "題",
    "达": "達", "别": "別", "为": "為", "观": "觀", "进": "進",
    "这": "這", "来": "來", "会": "會", "将": "將", "经": "經",
    "济": "濟", "应": "應", "关": "關", "键": "鍵", "动": "動",
    "没": "沒", "国": "國", "说": "說", "还": "還",
}


def normalize_traditional(text):
    return "".join(_SIMP_TO_TRAD.get(ch, ch) for ch in text)


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
    return normalize_traditional(payload["choices"][0]["message"]["content"].strip())


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


def build_ai_prompt_mr(name, code, industry, win_desc, r, related):
    """市場研究員：籌碼動向敘述"""
    lines = [
        f"你是台股market研究員，請用繁體中文（正體字，例如「機構」而非「机构」、「並非」而非「并非」，禁止出現任何簡體字）寫一段約80~120字的籌碼動向分析，語氣專業、精簡，不要條列、不要標題，只要一段完整敘述。",
        f"個股：{name}（{code}），產業：{industry or '未分類'}。",
        f"比較區間：{win_desc}。",
        f"{THRESHOLD}張以上大戶持股比：{r['pa']:.2f}% → {r['pb']:.2f}%（{'+' if r['dp']>=0 else ''}{r['dp']:.2f}個百分點）。",
        f"同期持股張數變化：{'+' if r['du']>=0 else ''}{r['du']:,}張，大戶人數變化：{'+' if r['dc']>=0 else ''}{r['dc']}人。",
    ]
    if related:
        titles = "、".join(v.get("title", "") for v in related if v.get("title"))
        if titles:
            lines.append(f"近期相關YouTube財經影片討論：{titles}。")
    lines.append("請直接給出分析內容，並在合適處提醒這只是籌碼面觀察，並非投資建議（請用「並非」而非簡體「并非」）。")
    return "\n".join(lines)


def build_ai_prompt_mb(name, code, industry, peers, rank, of_count, price_change_pct, holder_pp_change, alignment):
    """建模助手：同業比較評述"""
    peer_lines = "、".join(
        f"{p['code']}{p['name']}({p['pct']:.2f}%)" for p in (peers or [])
    )
    lines = [
        "你是台股建模助手，請用繁體中文（正體字，禁止出現任何簡體字）寫一段約80~120字的同業比較評述，語氣專業、精簡，不要條列、不要標題，只要一段完整敘述。",
        f"個股：{name}（{code}），產業：{industry or '未分類'}。",
        f"在同業中的{THRESHOLD}張以上大戶持股比排名：第{rank}名（共{of_count}檔）。",
        f"同業持股比排行（含自己）：{peer_lines or '資料不足'}。",
        f"區間股價變化：{('%.2f%%' % price_change_pct) if price_change_pct is not None else '資料不足'}，"
        f"大戶持股比變化：{'+' if holder_pp_change>=0 else ''}{holder_pp_change:.2f}個百分點。",
        f"籌碼與股價方向判斷：{alignment}。",
        "請針對這檔在同業中的籌碼集中度排名、以及籌碼與股價是否同向，給出評述，並提醒這只是簡易框架、非完整財務模型，非投資建議。",
    ]
    return "\n".join(lines)


def build_ai_prompt_er(name, code, earnings_videos):
    """財報審閱：統整已有的財報／法說相關YouTube討論重點（該股有本地影片資料時用）"""
    lines = [
        "你是台股財報審閱員，請用繁體中文（正體字，禁止出現任何簡體字）寫一段約80~150字，統整以下YouTube財經影片對這檔股票財報／法說會的討論重點，"
        "語氣專業、精簡，不要條列、不要標題，只要一段完整敘述。",
        f"個股：{name}（{code}）。",
        "相關影片內容：",
    ]
    for v in earnings_videos:
        gist = v.get("gist") or ""
        points = "；".join(v.get("key_points") or [])
        lines.append(f"- {v.get('title','')}（{v.get('channel','')}）：{gist} {points}".strip())
    lines.append("請統整這些影片的共同論點或分歧之處，並提醒這是根據YouTube影片內容整理、非官方財報數據，仍需自行查證公開資訊觀測站(MOPS)資料，非投資建議。")
    return "\n".join(lines)


def build_ai_prompt_er_no_signal(name, code, industry, direction, r):
    """財報審閱：該股沒有本地YouTube財報討論時，改用已知的籌碼資料生成量身查證提醒
    （不得杜撰任何財報數字或財測，只能根據下面提供的籌碼資料延伸）"""
    lines = [
        "你是台股財報審閱員，請用繁體中文（正體字，禁止出現任何簡體字）寫一段約80~120字的財報查證提醒，"
        "語氣專業、精簡，不要條列、不要標題，只要一段完整敘述。",
        f"個股：{name}（{code}），產業：{industry or '未分類'}。",
        f"目前沒有追蹤到本地YouTube頻道對這檔股票的財報／法說會討論。",
        f"已知資訊：近期{THRESHOLD}張以上大戶持股比{direction}，變動{'+' if r['dp']>=0 else ''}{r['dp']:.2f}個百分點。",
        "請只根據上述籌碼資訊，提醒投資人應主動至公開資訊觀測站(MOPS)查詢最新財報與重大訊息、留意近期是否有法說會，"
        "並可將籌碼變化方向與財報公布時間點交叉比對。"
        "禁止捏造任何財報數字、營收、EPS或財測，沒有的資訊就不要提。",
    ]
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

    ai_stats = {"mr": [0, 0], "mb": [0, 0], "er": [0, 0]}  # 每個分頁 [成功數, 失敗數]
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
                ai_text = call_local_ai(build_ai_prompt_mr(name, code, industry, win_desc, r, related))
                if ai_text:
                    narrative = ai_text
                    narrative_source = "ai"
                    ai_stats["mr"][0] += 1
            except Exception as e:
                ai_stats["mr"][1] += 1
                print(f"  [警告] {name}（{code}）市場研究員AI敘述生成失敗，改用規則式敘述：{e}")

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

        note = ("此為以大戶持股比＋股價區間資料建立的簡易同業比較框架，非完整財務模型（未含 EPS、營收、估值倍數），"
                "可作為進一步建模的起點。")
        mb_note_source = "rule"
        if args.ai and idx < AI_TOP_N:
            try:
                ai_text = call_local_ai(build_ai_prompt_mb(
                    name, code, industry, peers, rank, len(full_peers),
                    price_change_pct, r["dp"], alignment))
                if ai_text:
                    note = ai_text
                    mb_note_source = "ai"
                    ai_stats["mb"][0] += 1
            except Exception as e:
                ai_stats["mb"][1] += 1
                print(f"  [警告] {name}（{code}）建模助手AI評述生成失敗，改用規則式敘述：{e}")

        model_builder.append({
            "code": code, "name": name, "industry": industry, "market": market,
            "peer_rank": {"rank": rank, "of": len(full_peers), "threshold": THRESHOLD},
            "peers": peers,
            "price_change_pct": price_change_pct,
            "holder_pp_change": r["dp"],
            "alignment": alignment,
            "note_source": mb_note_source,
            "source_links": [{"label": "Yahoo 股市行情", "url": yurl}],
            "note": note,
        })

        # --- Earnings Reviewer ---
        earnings_videos = []
        for v, text in video_index:
            if name and name in text and any(k in text for k in EARNINGS_KEYWORDS):
                earnings_videos.append(video_brief(v))
        earnings_videos = earnings_videos[:MAX_VIDEOS_PER_STOCK]

        er_commentary, er_commentary_source = None, None
        if args.ai and idx < AI_TOP_N:
            try:
                if earnings_videos:
                    # 有本地YouTube財報／法說討論：AI統整影片重點
                    ai_text = call_local_ai(build_ai_prompt_er(name, code, earnings_videos))
                else:
                    # 沒有本地影片資料：AI只根據已知籌碼資料生成量身查證提醒，
                    # prompt明確禁止捏造財報數字，不會產生沒有根據的財務內容。
                    ai_text = call_local_ai(build_ai_prompt_er_no_signal(name, code, industry, direction, r))
                if ai_text:
                    er_commentary = ai_text
                    er_commentary_source = "ai"
                    ai_stats["er"][0] += 1
            except Exception as e:
                ai_stats["er"][1] += 1
                print(f"  [警告] {name}（{code}）財報審閱AI生成失敗，維持原始清單：{e}")

        earnings_reviewer.append({
            "code": code, "name": name, "market": market,
            "earnings_related_videos": earnings_videos,
            "has_local_signal": bool(earnings_videos),
            "commentary": er_commentary,
            "commentary_source": er_commentary_source,
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
        disclaimer += (f"其中三個分頁前 {AI_TOP_N} 檔（依籌碼變化幅度排序）的敘述／評述文字"
                        "由本機自架 AI 推論伺服器生成（財報審閱在有本地YouTube財報討論時由AI統整影片重點，"
                        "沒有本地影片資料則由AI根據已知籌碼資料生成查證提醒，不會捏造財報數字），"
                        "其餘個股維持規則式整理，AI 生成內容同樣僅供研究參考，非投資建議，請自行查證。")

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
        mr_ok, mr_fail = ai_stats["mr"]
        mb_ok, mb_fail = ai_stats["mb"]
        er_ok, er_fail = ai_stats["er"]
        print(f"AI生成統計：市場研究員 成功{mr_ok}/失敗{mr_fail}，"
              f"建模助手 成功{mb_ok}/失敗{mb_fail}，"
              f"財報審閱 成功{er_ok}/失敗{er_fail}（無本地影片資料時改為AI查證提醒；失敗已自動退回規則式內容）")


if __name__ == "__main__":
    main()
