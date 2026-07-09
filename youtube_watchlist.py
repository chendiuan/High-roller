# -*- coding: utf-8 -*-
"""
YouTube 新影片追蹤器

用法：
  python youtube_watchlist.py
  python youtube_watchlist.py --open

不需要 YouTube API key；優先使用 YouTube 官方 RSS feed。
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "youtube_channels.json")
STATE_PATH = os.path.join(BASE, "youtube_seen.json")
DATA_JS = os.path.join(BASE, "youtube_data.js")
INBOX_HTML = os.path.join(BASE, "youtube_inbox.html")
REPORT_DIR = os.path.join(BASE, "youtube_reports")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
LOCAL_TZ = ZoneInfo("Asia/Taipei") if ZoneInfo else None

ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"


def http_get_text(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    charset = "utf-8"
    ctype = resp.headers.get("content-type", "")
    m = re.search(r"charset=([\w-]+)", ctype)
    if m:
        charset = m.group(1)
    return raw.decode(charset, errors="replace")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(str(text).encode(enc, errors="replace").decode(enc, errors="replace"))


def channel_id_from_url(url):
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/")
    m = re.search(r"(?:^|/)channel/(UC[\w-]{20,})", "/" + path)
    if m:
        return m.group(1)
    if path.startswith("UC") and len(path) >= 20:
        return path.split("/")[0]
    return None


def resolve_channel_id(channel):
    if channel.get("channel_id"):
        return channel["channel_id"].strip()
    url = (channel.get("url") or "").strip()
    direct = channel_id_from_url(url)
    if direct:
        return direct
    if not url:
        raise ValueError("缺少 url、channel_id 或 feed_url")
    page = http_get_text(url)
    patterns = [
        r'"channelId"\s*:\s*"(UC[\w-]{20,})"',
        r'"externalId"\s*:\s*"(UC[\w-]{20,})"',
        r'<meta itemprop="channelId" content="(UC[\w-]{20,})"',
        r'https://www\.youtube\.com/channel/(UC[\w-]{20,})',
    ]
    for pat in patterns:
        m = re.search(pat, page)
        if m:
            return m.group(1)
    raise ValueError("無法從 YouTube 頁面解析 channel_id，請改填 /channel/UC... 或 feed_url")


def feed_url_for(channel):
    if channel.get("feed_url"):
        return channel["feed_url"].strip()
    cid = resolve_channel_id(channel)
    return "https://www.youtube.com/feeds/videos.xml?channel_id=" + urllib.parse.quote(cid)


def text_of(el, path, default=""):
    node = el.find(path)
    return node.text.strip() if node is not None and node.text else default


def parse_feed(xml_text):
    root = ET.fromstring(xml_text)
    channel_title = text_of(root, ATOM + "title")
    videos = []
    for entry in root.findall(ATOM + "entry"):
        vid = text_of(entry, YT + "videoId")
        title = text_of(entry, ATOM + "title")
        url = text_of(entry, ATOM + "link")
        link = entry.find(ATOM + "link")
        if link is not None:
            url = link.attrib.get("href", url)
        published = text_of(entry, ATOM + "published")
        updated = text_of(entry, ATOM + "updated")
        group = entry.find(MEDIA + "group")
        description = ""
        thumbnail = ""
        if group is not None:
            description = text_of(group, MEDIA + "description")
            thumb = group.find(MEDIA + "thumbnail")
            if thumb is not None:
                thumbnail = thumb.attrib.get("url", "")
        videos.append({
            "id": vid,
            "title": title,
            "url": url or ("https://www.youtube.com/watch?v=" + vid if vid else ""),
            "published": published,
            "updated": updated,
            "description": description,
            "thumbnail": thumbnail,
        })
    return channel_title, videos


def iso_to_local_label(value):
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M") if LOCAL_TZ else dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def now_label():
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S") if LOCAL_TZ else datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_stamp():
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds") if LOCAL_TZ else datetime.now().isoformat(timespec="seconds")


def clean_description(description):
    lines = []
    for raw in (description or "").replace("\r\n", "\n").split("\n"):
        line = re.sub(r"https?://\S+", "", raw).strip()
        if not line or line.startswith("#"):
            continue
        if any(key in line.lower() for key in ("http", "訂閱", "subscribe", "加入會員", "工商", "合作邀約")):
            continue
        lines.append(line)
    return lines


def make_video_analysis(video):
    title = video.get("title", "").strip()
    desc_lines = clean_description(video.get("description", ""))
    title_parts = [p.strip(" -｜|:：") for p in re.split(r"[｜|/／:：\-]", title) if p.strip()]
    points = []
    for part in title_parts:
        if 4 <= len(part) <= 42 and part not in points:
            points.append(part)
    for line in desc_lines:
        if len(points) >= 6:
            break
        if 6 <= len(line) <= 80 and line not in points:
            points.append(line)
    if not points and title:
        points.append(title)
    signals = []
    text = title + "\n" + "\n".join(desc_lines)
    if re.search(r"\d+(?:\.\d+)?\s*[%％]", text):
        signals.append("含百分比或報酬率敘述，觀看時要確認期間、基準與樣本。")
    if re.search(r"\d{4}", text):
        signals.append("可能提到個股代號，適合搭配本頁大戶持股與股價區間交叉檢查。")
    if any(k in text for k in ("崩盤", "暴漲", "噴", "急跌", "利空", "利多")):
        signals.append("標題帶有強烈行情語氣，建議把情緒判斷和實際數據分開看。")
    if not signals:
        signals.append("先確認影片中的主要論點、數據來源，以及是否有可驗證的投資假設。")
    gist = f"這支影片看起來聚焦在「{points[0] if points else title}」。"
    if len(points) > 1:
        gist += " 可優先留意標題與描述中反覆出現的主題。"
    return {
        "basis": "根據 YouTube RSS 提供的標題、描述與發布時間整理；尚未取得逐字稿或完整影音內容。",
        "gist": gist,
        "key_points": points[:6],
        "watch_focus": signals[:4],
        "verify": [
            "影片中的個股、產業或大盤判斷是否有明確資料來源。",
            "若提到財報、籌碼、技術線型或政策消息，觀看後再回到原始資料查證。",
            "此整理不是投資建議，只是幫你決定觀看順序與記錄重點。"
        ]
    }


def video_matches_keywords(video, keywords):
    if not keywords:
        return True
    haystack = (video.get("title", "") + "\n" + video.get("description", "")).lower()
    return any(str(k).lower() in haystack for k in keywords)


def render_inbox(payload):
    cards = []
    for video in payload["new_videos"]:
        thumb = html.escape(video.get("thumbnail") or "")
        title = html.escape(video.get("title") or "")
        channel = html.escape(video.get("channel_name") or "")
        url = html.escape(video.get("url") or "")
        published = html.escape(iso_to_local_label(video.get("published") or ""))
        desc = html.escape((video.get("description") or "").strip())
        if len(desc) > 220:
            desc = desc[:220] + "..."
        cards.append(f"""
        <article class="video">
          <a class="thumb" href="{url}" target="_blank" rel="noreferrer">
            {'<img src="' + thumb + '" alt="">' if thumb else ''}
          </a>
          <div>
            <div class="channel">{channel}</div>
            <h2><a href="{url}" target="_blank" rel="noreferrer">{title}</a></h2>
            <div class="published">{published}</div>
            <p><b>一句話：</b>{html.escape(video.get('analysis', {}).get('gist', ''))}</p>
            <p>{desc}</p>
            <a class="watch" href="{url}" target="_blank" rel="noreferrer">觀看影片</a>
          </div>
        </article>""")
    empty = '<div class="empty">今天沒有偵測到新上傳影片。</div>'
    html_text = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YouTube 每日待看清單</title>
<style>
:root{{--bg:#0b0f1a;--panel:#151b2b;--line:#27314b;--ink:#edf1f7;--sub:#9aa4b8;--accent:#f5a623}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Segoe UI","Microsoft JhengHei",sans-serif;padding:24px;max-width:1040px;margin-inline:auto}}
header{{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:18px}}
h1{{font-size:24px;margin:0}} .meta{{color:var(--sub);font-size:13px;margin-top:6px}}
.video{{display:grid;grid-template-columns:220px 1fr;gap:18px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:12px}}
.thumb{{display:block;aspect-ratio:16/9;background:#0f1421;border-radius:8px;overflow:hidden}} .thumb img{{width:100%;height:100%;object-fit:cover}}
.channel,.published{{color:var(--sub);font-size:13px}} h2{{font-size:18px;line-height:1.35;margin:4px 0}} h2 a{{color:var(--ink);text-decoration:none}}
p{{color:#c8cfdd;font-size:14px;line-height:1.6;margin:8px 0 12px}} .watch{{display:inline-block;color:#201500;background:var(--accent);font-weight:700;text-decoration:none;border-radius:18px;padding:7px 14px;font-size:13px}}
.empty{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:34px;text-align:center;color:var(--sub)}}
@media (max-width:720px){{body{{padding:14px}} header{{display:block}} .video{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header>
  <div>
    <h1>YouTube 每日待看清單</h1>
    <div class="meta">更新時間：{html.escape(payload["generated"])} · 新影片 {len(payload["new_videos"])} 支</div>
  </div>
</header>
{''.join(cards) if cards else empty}
</body>
</html>"""
    with open(INBOX_HTML, "w", encoding="utf-8") as f:
        f.write(html_text)


def write_report(payload):
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    path = os.path.join(REPORT_DIR, f"youtube_new_videos_{today}.md")
    lines = [
        "# YouTube 每日待看清單",
        "",
        f"- 更新時間：{payload['generated']}",
        f"- 新影片：{len(payload['new_videos'])} 支",
        "",
    ]
    if not payload["new_videos"]:
        lines.append("今天沒有偵測到新上傳影片。")
    for video in payload["new_videos"]:
        lines.extend([
            f"## {video.get('title', '')}",
            "",
            f"- 頻道：{video.get('channel_name', '')}",
            f"- 發布：{iso_to_local_label(video.get('published', ''))}",
            f"- 連結：{video.get('url', '')}",
            "",
            "### 一句話",
            "",
            video.get("analysis", {}).get("gist", ""),
            "",
            "### 摘要筆記",
            "",
            *[f"- {p}" for p in video.get("analysis", {}).get("key_points", [])],
            "",
            "### 觀看與查證重點",
            "",
            *[f"- {p}" for p in video.get("analysis", {}).get("watch_focus", [])],
            "",
        ])
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true", help="偵測完成後開啟新影片與待看清單")
    args = parser.parse_args()

    config = load_json(CONFIG_PATH, {})
    channels = [c for c in config.get("channels", []) if c.get("enabled", True)]
    if not channels:
        print("沒有啟用的 YouTube 頻道。請編輯 youtube_channels.json。")
        return 1

    state = load_json(STATE_PATH, {"seen_ids": [], "videos": {}})
    seen = set(state.get("seen_ids", []))
    first_run = not os.path.exists(STATE_PATH)
    new_videos = []
    errors = []
    max_items = int(config.get("max_videos_per_channel", 10))

    for channel in channels:
        try:
            feed_url = feed_url_for(channel)
            channel_title, videos = parse_feed(http_get_text(feed_url))
            channel_name = channel.get("name") or channel_title
            keywords = channel.get("keywords") or config.get("keywords") or []
            for video in videos[:max_items]:
                if not video.get("id") or not video_matches_keywords(video, keywords):
                    continue
                video["channel_name"] = channel_name
                video["channel_url"] = channel.get("url", "")
                video["analysis"] = make_video_analysis(video)
                state.setdefault("videos", {})[video["id"]] = video
                if video["id"] not in seen:
                    new_videos.append(video)
                seen.add(video["id"])
        except Exception as exc:
            name = channel.get("name") or channel.get("url") or channel.get("channel_id") or "未命名頻道"
            errors.append({"channel": name, "error": str(exc)})
            safe_print(f"!! {name}: {exc}")

    if first_run and config.get("baseline_existing_on_first_run", False):
        new_videos = []

    state["seen_ids"] = sorted(seen)
    state["last_run"] = now_stamp()
    save_json(STATE_PATH, state)

    recent_videos = sorted(
        state.get("videos", {}).values(),
        key=lambda v: v.get("published", ""),
        reverse=True,
    )

    payload = {
        "generated": now_label(),
        "new_videos": sorted(new_videos, key=lambda v: v.get("published", ""), reverse=True),
        "recent_videos": recent_videos,
        "errors": errors,
        "source_basis": "根據 YouTube RSS 的標題、描述與發布時間整理；未下載影音，未取得逐字稿。",
    }
    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write("window.YOUTUBE_WATCHLIST=")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";")
    render_inbox(payload)
    report_path = write_report(payload)

    safe_print(f"新影片：{len(payload['new_videos'])} 支")
    for video in payload["new_videos"]:
        safe_print(f"- [{video.get('channel_name', '')}] {video.get('title', '')}")
        safe_print(f"  {video.get('url', '')}")
    safe_print(f"已更新：{INBOX_HTML}")
    safe_print(f"已產生：{report_path}")

    should_open = args.open or config.get("open_new_videos", False)
    if should_open:
        webbrowser.open("file:///" + INBOX_HTML.replace("\\", "/"))
        for video in payload["new_videos"]:
            webbrowser.open(video["url"])
            time.sleep(1)
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
