"""Reproducible crawler for the Guanyin District local-knowledge assignment.

Uses public pages only, obeys robots.txt, applies conservative rate limits, and
records failures rather than inventing or silently dropping content.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
UA = "GuanyinKnowledgeCrawler/1.0 (educational project; contact: student)"
TIMEOUT = 20
MAX_IMAGE_BYTES = 12 * 1024 * 1024
RELEVANT = re.compile(r"觀音|桃園|白沙岬|甘泉寺|坑溪|大堀溪|大潭|藻礁|蓮花|客家|文化|燈塔|觀光|歷史|景點|Guanyin|Taoyuan", re.I)
SKIP_LINK = re.compile(r"javascript:|mailto:|tel:|\.pdf(?:$|\?)|logout|login", re.I)
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.5"})
robots_cache: dict[str, RobotFileParser | None] = {}
image_url_cache: dict[str, dict] = {}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clean_url(url: str) -> str:
    return urldefrag(url.strip())[0]


def allowed(url: str) -> tuple[bool, str]:
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    if origin not in robots_cache:
        rp = RobotFileParser()
        rp.set_url(urljoin(origin, "/robots.txt"))
        try:
            res = session.get(rp.url, timeout=TIMEOUT)
            if res.status_code == 200:
                rp.parse(res.text.splitlines())
                robots_cache[origin] = rp
            elif res.status_code in (401, 403):
                robots_cache[origin] = None
                return False, f"robots.txt 回應 {res.status_code}，停止存取"
            else:
                # When robots.txt is unavailable, do not treat it as a block.
                robots_cache[origin] = None
        except requests.RequestException:
            robots_cache[origin] = None
        time.sleep(0.4)
    rp = robots_cache[origin]
    if rp is not None and not rp.can_fetch(UA, url):
        return False, "robots.txt 不允許此路徑"
    return True, ""


def title_and_source(soup: BeautifulSoup, url: str) -> tuple[str, str]:
    title = (soup.find("meta", property="og:title") or {}).get("content")
    title = title or (soup.title.get_text(" ", strip=True) if soup.title else "")
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else "未能辨識標題"
    source = (soup.find("meta", property="og:site_name") or {}).get("content") or urlparse(url).netloc
    return title.strip(), source.strip()


def extract_article(soup: BeautifulSoup) -> tuple[str, str]:
    for tag in soup.select("script,style,noscript,nav,header,footer,aside,form,button,svg,iframe"):
        tag.decompose()
    for tag in soup.select("[aria-hidden='true'],[role='navigation'],[role='banner'],[role='contentinfo']"):
        tag.decompose()
    for tag in soup.select(".ad,.ads,.advertisement,.social-share,.related,.recommend,.cookie,.newsletter,.subscribe"):
        tag.decompose()
    candidates = soup.select("article,main,[role='main']")
    container = max(candidates, key=lambda n: len(n.get_text(" ", strip=True)), default=soup.body or soup)
    headings, chunks = [], []
    for node in container.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "table"]):
        text = node.get_text(" ", strip=True)
        if len(text) < 18:
            continue
        if node.name.startswith("h"):
            headings.append(text)
            chunks.append("#" * min(int(node.name[1]), 4) + " " + text)
        elif node.name == "li":
            chunks.append("- " + text)
        elif node.name == "table":
            chunks.append("[表格內容] " + text)
        else:
            chunks.append(text)
    content = "\n\n".join(dict.fromkeys(chunks))
    if not content:
        # Conservative fallback; the report marks that extraction needed review.
        content = container.get_text("\n", strip=True)
    return content[:100_000], "; ".join(dict.fromkeys(headings))


def parse_page(url: str, response: requests.Response, record_id: str) -> tuple[dict, list[str]]:
    soup = BeautifulSoup(response.content, "html.parser")
    title, source = title_and_source(soup, url)
    content, _ = extract_article(soup)
    canonical = soup.find("link", rel="canonical")
    image_nodes = soup.select("article img, main img, [role='main'] img") or soup.find_all("img")
    images, image_candidates = [], []
    for img in image_nodes:
        raw = next((img.get(k) for k in ("src", "data-src", "data-lazy-src", "data-original") if img.get(k)), "")
        if not raw or raw.startswith("data:"):
            continue
        src = clean_url(urljoin(response.url, raw))
        alt = (img.get("alt") or img.get("title") or "").strip()
        if any(x in (src + " " + alt).lower() for x in ("logo", "banner", "avatar", "icon", "sprite", "pixel", "share", "aa2.0", "accessibility", "a11y", "無障礙標章")):
            continue
        image_candidates.append((src, alt))
    links = []
    for a in soup.find_all("a", href=True):
        href = clean_url(urljoin(response.url, a["href"]))
        if href.startswith(("http://", "https://")) and not SKIP_LINK.search(href):
            links.append({"url": href, "text": a.get_text(" ", strip=True)[:200]})
    record = {
        "id": record_id, "type": "webpage", "title": title, "source_url": url,
        "canonical_url": canonical.get("href") if canonical else "", "source_name": source,
        "content": content, "images": images, "image_candidates": list(dict.fromkeys(image_candidates)),
        "links": list({x["url"]: x for x in links}.values()), "timestamps": [],
        "crawl_status": "success" if len(content) >= 100 else "partial",
        "status_reason": "" if len(content) >= 100 else "正文抽取內容偏少，建議人工查看原頁",
        "http_status": response.status_code, "image_scan_status": "detected_candidates", "crawled_at": now(),
    }
    return record, [x["url"] for x in record["links"]]


def save_image(url: str, record: dict, number: int) -> dict:
    if url in image_url_cache:
        return dict(image_url_cache[url])
    ok, reason = allowed(url)
    if not ok:
        return {"url": url, "saved": False, "reason": reason}
    try:
        r = session.get(url, timeout=TIMEOUT, stream=True)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "").split(";")[0].lower()
        if not ctype.startswith("image/") or ctype == "image/svg+xml":
            return {"url": url, "saved": False, "reason": f"非支援圖片類型：{ctype or '未知'}"}
        data = bytearray()
        for block in r.iter_content(64 * 1024):
            data.extend(block)
            if len(data) > MAX_IMAGE_BYTES:
                return {"url": url, "saved": False, "reason": "超過 12 MB 上限"}
        if len(data) < 2048:
            return {"url": url, "saved": False, "reason": "檔案過小，視為圖示或追蹤素材"}
        digest = hashlib.sha256(data).hexdigest()
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(ctype, ".img")
        target = OUT / "images" / f"{record['id']}_image_{number:02d}_{digest[:8]}{ext}"
        if not target.exists():
            target.write_bytes(data)
        result = {"url": url, "file": str(target.relative_to(ROOT)).replace("\\", "/"), "alt": "", "sha256": digest, "saved": True, "reason": ""}
        image_url_cache[url] = result
        return dict(result)
    except requests.RequestException as exc:
        return {"url": url, "saved": False, "reason": f"圖片請求失敗：{exc.__class__.__name__}: {exc}"}


def youtube(url: str, record_id: str) -> dict:
    record = {"id": record_id, "type": "video", "title": "", "source_url": url,
              "source_name": "YouTube", "content": "", "images": [], "links": [],
              "timestamps": [], "crawl_status": "unavailable", "status_reason": "",
              "crawled_at": now(), "transcript": "", "frames": [], "http_status": ""}
    try:
        r = session.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"}, timeout=TIMEOUT)
        record["http_status"] = r.status_code
        if r.ok:
            data = r.json()
            record.update(title=data.get("title", ""), source_name=data.get("author_name", "YouTube"),
                          crawl_status="partial", status_reason="已取得 oEmbed 基本資料；字幕及畫面未透過下載或繞過限制擷取")
        else:
            record["status_reason"] = f"YouTube oEmbed 回應 HTTP {r.status_code}；未取得影片資料、字幕或畫面"
    except requests.RequestException as exc:
        record["status_reason"] = f"YouTube oEmbed 存取失敗：{exc.__class__.__name__}: {exc}"
    return record


def write_record(record: dict) -> None:
    (OUT / "articles").mkdir(parents=True, exist_ok=True)
    if record["type"] == "video":
        folder, suffix = OUT / "videos", ".md"
    else:
        folder, suffix = OUT / "articles", ".md"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{record['id']}{suffix}"
    lines = [f"# {record.get('title') or '（未取得標題）'}", "", f"- 原始網址：{record['source_url']}",
             f"- 來源：{record.get('source_name','')}", f"- 狀態：{record['crawl_status']}｜{record.get('status_reason','')}", ""]
    if record["type"] == "video":
        lines += ["## 字幕與代表畫面", "", "目前未擷取字幕或影片畫面。若有合法取得的字幕/素材，請附來源與授權資訊後補入。"]
    else:
        if record.get("content"):
            body = record["content"]
        elif record.get("crawl_status") == "unavailable":
            body = "（來源無法存取，未取得正文；請參考狀態原因。）"
        else:
            body = "（未擷取到正文；本筆標記為部分成功，請檢查原始頁面。）"
        lines += ["## 清理後正文", "", body]
        lines += ["", "## 圖片", ""]
        images = record.get("images", [])
        for item in images:
            if item.get("saved"):
                lines.append(f"- {item.get('file') or item['url']} — {item.get('alt') or '無圖片說明'}")
            else:
                lines.append(f"- 未保存：{item.get('url', '')} — {item.get('reason') or '未提供原因'}")
        if not images:
            if record.get("crawl_status") == "unavailable":
                lines.append("來源無法存取，未能檢查頁面圖片。")
            else:
                lines.append("未發現可保存的文章圖片；完整候選清單見 index.jsonl 的 image_candidates 欄位。")
    p.write_text("\n".join(lines), encoding="utf-8")


def classify_and_deduplicate(records: list[dict]) -> None:
    themes = {
        "自然景觀": ["海岸", "濕地", "溪", "藻礁", "生態", "風景", "沙丘", "植物", "自然"],
        "歷史文化": ["歷史", "寺", "文化", "古蹟", "信仰", "客家", "原住民", "祭典", "傳統"],
        "地方產業": ["產業", "農業", "漁業", "蓮花", "工業", "電廠", "農場", "美食"],
        "觀光景點": ["景點", "旅遊", "觀光", "燈塔", "公園", "步道", "遊客", "交通"],
    }
    words = list(dict.fromkeys(sum(themes.values(), []) + ["觀音", "桃園", "白沙岬", "甘泉寺", "大潭", "大堀溪", "坑溪"]))
    title_hints = {
        "自然景觀": ["沙丘", "藻礁", "海岸", "溪", "綠韻大道"],
        "歷史文化": ["歷史", "移民", "原住民", "寺", "文化", "燈塔"],
        "地方產業": ["產業", "牛家莊", "美食", "農場", "蓮花", "電廠"],
        "觀光景點": ["旅遊", "景點", "觀光", "小旅行", "樹園子", "百合花海"],
    }
    keyword_hints = {
        "牛家莊": ["客庄", "地方產業"], "百合": ["百合花卉", "休閒旅遊"],
        "原住民": ["原住民族", "文化文獻"], "移民": ["遷村", "地方歷史"],
        "歷史": ["地方歷史", "沿革"], "分區": ["地區介紹", "旅遊資訊"],
    }
    for record in records:
        title = record.get("title", "")
        sample = f"{title} {record.get('content','')}".lower()
        matches = [(sum(sample.count(term.lower()) for term in terms), name) for name, terms in themes.items()]
        best_score, best_category = max(matches, default=(0, "待人工分類"))
        if best_score == 0:
            for category, hints in title_hints.items():
                if any(hint in title for hint in hints):
                    best_category = category
                    break
        record["topic_category"] = best_category
        counts = [(sample.count(w.lower()), w) for w in words if sample.count(w.lower())]
        keywords = [w for _, w in sorted(counts, reverse=True)[:5]]
        for hint, additions in keyword_hints.items():
            if hint in title:
                keywords.extend(additions)
        fallback = ["觀音區", "地方知識", "待人工檢視"]
        if record.get("crawl_status") == "unavailable":
            fallback = ["觀音區", "來源受限", "待查核"]
        keywords.extend(fallback)
        record["keywords"] = list(dict.fromkeys(keywords))[:5]
        record["duplicate_of"] = ""
    for i, current in enumerate(records):
        title = re.sub(r"\W", "", current.get("title", "").lower())
        body = re.sub(r"\s+", "", current.get("content", "").lower())[:3000]
        if not (title or body):
            continue
        for previous in records[:i]:
            if previous.get("crawl_status") not in ("success", "partial"):
                continue
            old_title = re.sub(r"\W", "", previous.get("title", "").lower())
            old_body = re.sub(r"\s+", "", previous.get("content", "").lower())[:3000]
            # Short pages often share boilerplate (navigation and app notices); do
            # not call them duplicates based on boilerplate alone.
            if (title and old_title and difflib.SequenceMatcher(None, title, old_title).ratio() >= .90) or (len(body) >= 500 and len(old_body) >= 500 and difflib.SequenceMatcher(None, body, old_body).ratio() >= .92):
                current["duplicate_of"] = previous["id"]
                break


def main() -> int:
    global OUT
    ap = argparse.ArgumentParser(description="爬取 urls.csv 中的公開網頁與 YouTube 基本資料")
    ap.add_argument("--urls", type=Path, default=ROOT / "urls.csv")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "output", help="指定輸出目錄，方便將 demo 測試結果與正式成果分開")
    ap.add_argument("--discover", action="store_true", help="同網域探索一層相關連結；最多 2 個/種子、總計 20 個")
    ap.add_argument("--no-images", action="store_true", help="只記錄圖片候選，不下載圖片")
    ap.add_argument("--clean-output", action="store_true", help="先清除 output/articles、output/images、output/videos 舊檔，再開始本次擷取")
    args = ap.parse_args()
    OUT = args.output_dir.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.clean_output:
        for name in ("articles", "images", "videos"):
            folder = OUT / name
            if folder.exists():
                shutil.rmtree(folder)
            folder.mkdir(parents=True, exist_ok=True)
    with args.urls.open(encoding="utf-8-sig", newline="") as f:
        urls = list(dict.fromkeys(clean_url(row["url"]) for row in csv.DictReader(f) if row.get("url", "").strip()))
    records, discovered = [], []
    for i, url in enumerate(urls, 1):
        rid = f"page_{i:03d}"
        print(f"[{i}/{len(urls)}] {url}", flush=True)
        if "youtube.com" in urlparse(url).netloc.lower() or "youtu.be" in urlparse(url).netloc.lower():
            rec = youtube(url, rid)
        else:
            ok, reason = allowed(url)
            if not ok:
                rec = {"id": rid, "type": "webpage", "title": "", "source_url": url, "source_name": urlparse(url).netloc,
                       "content": "", "images": [], "links": [], "timestamps": [], "crawl_status": "unavailable",
                       "status_reason": reason, "image_scan_status": "not_attempted_source_unavailable", "crawled_at": now()}
            else:
                try:
                    r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
                    r.raise_for_status()
                    if "html" not in r.headers.get("Content-Type", "").lower():
                        raise ValueError(f"非 HTML 內容：{r.headers.get('Content-Type','未知')}")
                    rec, links = parse_page(url, r, rid)
                    if args.discover:
                        host = urlparse(r.url).netloc
                        relevant = [x for x in links if urlparse(x).netloc == host and RELEVANT.search(x)]
                        discovered.extend(relevant[:2])
                    if not args.no_images:
                        for n, (img_url, alt) in enumerate(rec.get("image_candidates", []), 1):
                            img = save_image(img_url, rec, n)
                            img["alt"] = alt
                            rec["images"].append(img)
                        rec["image_scan_status"] = "completed"
                    else:
                        rec["image_scan_status"] = "download_skipped_by_option"
                except (requests.RequestException, ValueError) as exc:
                    rec = {"id": rid, "type": "webpage", "title": "", "source_url": url, "source_name": urlparse(url).netloc,
                           "content": "", "images": [], "links": [], "timestamps": [], "crawl_status": "failed",
                           "status_reason": f"{exc.__class__.__name__}: {exc}", "crawled_at": now()}
        records.append(rec)
        write_record(rec)
        time.sleep(1.0)
    # Optional, bounded depth-one exploration, deduplicated and same-origin to its seed.
    if args.discover:
        known = set(urls)
        for url in dict.fromkeys(discovered):
            if url in known or len(records) >= len(urls) + 20:
                continue
            known.add(url)
            rid = f"extra_{len(records)-len(urls)+1:03d}"
            try:
                ok, reason = allowed(url)
                if not ok:
                    raise PermissionError(reason)
                r = session.get(url, timeout=TIMEOUT, allow_redirects=True); r.raise_for_status()
                rec, _ = parse_page(url, r, rid)
                rec["discovery_depth"] = 1
            except Exception as exc:
                rec = {"id": rid, "type": "webpage", "title": "", "source_url": url, "source_name": urlparse(url).netloc,
                       "content": "", "images": [], "links": [], "timestamps": [], "crawl_status": "failed",
                       "status_reason": f"延伸探索：{exc.__class__.__name__}: {exc}", "crawled_at": now(), "discovery_depth": 1}
            records.append(rec); write_record(rec); time.sleep(1.0)
    classify_and_deduplicate(records)
    for rec in records:
        write_record(rec)
    with (OUT / "index.jsonl").open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with (OUT / "crawl_report.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["id", "type", "title", "source_url", "source_name", "crawl_status", "status_reason", "http_status", "crawled_at"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for rec in records: w.writerow({k: rec.get(k, "") for k in fields})
    print(f"完成：{len(records)} 筆；輸出位於 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
