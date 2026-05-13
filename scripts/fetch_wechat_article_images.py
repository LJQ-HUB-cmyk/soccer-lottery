#!/usr/bin/env python3
"""
从微信公众号文章 HTML 的 #js_content 中解析图片 URL，下载到本地目录。
供助手随后用 Read/多模态逐张读图（不跑 Tesseract）。

用法：
  python3 fetch_wechat_article_images.py --url 'https://mp.weixin.qq.com/s/xxxx'
  curl -sL -A 'Mozilla/5.0 …' 'URL' | python3 fetch_wechat_article_images.py --html-stdin

标准输出末尾为机器可读块（便于编排）：
  WECHAT_IMAGES_OUT_DIR=<绝对路径>
  WECHAT_IMAGES_LIST_FILE=<绝对路径>（每行一个图片绝对路径，按文中顺序）
"""
from __future__ import annotations

import argparse
import html as html_module
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _read_html(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def _fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=45)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _js_content_fragment(raw: str) -> str | None:
    m = re.search(r'id="js_content"[^>]*>(.*?)</div>\s*<script', raw, re.S)
    return m.group(1) if m else None


def _title_slug(raw: str) -> str:
    t = None
    m = re.search(r'id="activity-name"[^>]*>.*?<span[^>]*>([^<]+)</span>', raw, re.S)
    if m:
        t = m.group(1).strip()
    if not t:
        m = re.search(r"var msg_title = ['\"]([^'\"]+)['\"]", raw)
        if m:
            t = m.group(1).strip()
    if not t:
        t = "article"
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "_", t).strip("_")[:80] or "article"
    return t


def _extract_image_urls(js_html: str) -> list[str]:
    soup = BeautifulSoup(js_html, "lxml")
    seen: set[str] = set()
    out: list[str] = []
    for img in soup.find_all("img"):
        url = img.get("data-src") or img.get("src")
        if not url or not url.startswith("http"):
            continue
        url = html_module.unescape(url.split("#")[0])
        if "wx_fmt=gif" in url.lower():
            continue
        if not any(h in url for h in ("mmbiz.qpic.cn", "mmbiz.qlogo.cn", "qpic.cn")):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _guess_suffix(url: str, content_type: str | None) -> str:
    low = url.lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        if ext in low.split("?")[0][-8:]:
            return ext if ext != ".jpeg" else ".jpg"
    if content_type:
        if "png" in content_type:
            return ".png"
        if "jpeg" in content_type or "jpg" in content_type:
            return ".jpg"
        if "webp" in content_type:
            return ".webp"
    return ".jpg"


def main() -> None:
    ap = argparse.ArgumentParser(description="Download WeChat article body images from HTML.")
    ap.add_argument("--url", help="Article URL to fetch")
    ap.add_argument("--html-file", help="Local HTML file")
    ap.add_argument("--html-stdin", action="store_true", help="Read HTML from stdin")
    ap.add_argument(
        "--out",
        help="Output directory (default: skill_root/.cache/wechat_images/<slug>_<ts>)",
    )
    ap.add_argument("--max-images", type=int, default=30, help="Max images to download (default 30)")
    ap.add_argument("--max-bytes", type=int, default=8_000_000, help="Max bytes per image (default 8MB)")
    args = ap.parse_args()

    if bool(args.url) + bool(args.html_file) + bool(args.html_stdin) != 1:
        ap.error("请只指定其一：--url、--html-file 或 --html-stdin")

    skill_root = Path(__file__).resolve().parent.parent

    if args.url:
        raw = _fetch_html(args.url)
    elif args.html_file:
        raw = _read_html(args.html_file)
    else:
        raw = sys.stdin.read()

    if "环境异常" in raw and "js_content" not in raw:
        print("ERROR: 微信验证页（环境异常），无法解析正文。", file=sys.stderr)
        sys.exit(2)

    frag = _js_content_fragment(raw)
    if not frag:
        print("ERROR: 未找到 #js_content。", file=sys.stderr)
        sys.exit(1)

    urls = _extract_image_urls(frag)
    if not urls:
        print("ERROR: #js_content 内无可用图片 URL。", file=sys.stderr)
        sys.exit(1)

    slug = _title_slug(raw)
    ts = int(time.time())
    if args.out:
        out_dir = Path(args.out).resolve()
    else:
        out_dir = skill_root / ".cache" / "wechat_images" / f"{slug}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    saved: list[Path] = []
    for i, url in enumerate(urls[: args.max_images], start=1):
        try:
            resp = session.get(url, timeout=45, stream=True)
            resp.raise_for_status()
            total = 0
            chunks: list[bytes] = []
            for chunk in resp.iter_content(64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > args.max_bytes:
                    raise ValueError("exceeds max-bytes")
                chunks.append(chunk)
            data = b"".join(chunks)
            suf = _guess_suffix(url, resp.headers.get("Content-Type"))
            path = out_dir / f"{i:03d}{suf}"
            path.write_bytes(data)
            saved.append(path)
        except Exception as ex:
            print(f"WARN: skip image {i}: {ex}", file=sys.stderr)

    if not saved:
        print("ERROR: 全部下载失败。", file=sys.stderr)
        sys.exit(1)

    list_file = out_dir / "_images.txt"
    list_file.write_text("\n".join(str(p) for p in saved) + "\n", encoding="utf-8")

    print(f"Downloaded {len(saved)} image(s) -> {out_dir}")
    for p in saved:
        print(p)
    print(f"WECHAT_IMAGES_OUT_DIR={out_dir}")
    print(f"WECHAT_IMAGES_LIST_FILE={list_file}")


if __name__ == "__main__":
    main()
