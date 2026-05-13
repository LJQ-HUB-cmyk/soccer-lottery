#!/usr/bin/env python3
"""
从微信公众号文章 HTML 中提取标题、账号名与 #js_content 内可见文本。
用法：
  curl -sL -A 'Mozilla/5.0' 'https://mp.weixin.qq.com/s/xxxx' | python3 parse_wechat_article_html.py
  python3 parse_wechat_article_html.py /path/to/article.html
  python3 parse_wechat_article_html.py --url 'https://mp.weixin.qq.com/s/xxxx'
若正文多为长图、HTML 几乎无字：运行 `fetch_wechat_article_images.py --url '…'` 下载 `#js_content` 内配图，再由助手 Read 逐张读图解析（不跑本地 OCR）。
"""
from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _read_html(path: str | None) -> str:
    if path:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    return sys.stdin.read()


def _fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=45)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _js_content_fragment(raw: str) -> str | None:
    m = re.search(r'id="js_content"[^>]*>(.*?)</div>\s*<script', raw, re.S)
    return m.group(1) if m else None


def _extract_between(html: str, pattern: str, flags: int = 0) -> str | None:
    m = re.search(pattern, html, flags)
    return m.group(1).strip() if m else None


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        t = data.strip()
        if t:
            self.chunks.append(t)


def _html_to_text(fragment: str) -> str:
    p = _Stripper()
    p.feed(fragment)
    seen: set[str] = set()
    out: list[str] = []
    for c in p.chunks:
        if c not in seen and len(c) > 1:
            seen.add(c)
            out.append(c)
    return "\n".join(out)


def _emit_meta_and_text(raw: str) -> None:
    if "环境异常" in raw and "js_content" not in raw:
        print("ERROR: 页面为微信验证页（环境异常），请用浏览器打开后复制全文粘贴，或换网络重试。")
        sys.exit(2)

    title = _extract_between(
        raw,
        r'id="activity-name"[^>]*>.*?<span[^>]*>([^<]+)</span>',
        re.S,
    ) or _extract_between(raw, r'<h1[^>]*rich_media_title[^>]*>([^<]+)</h1>', re.S)
    if not title:
        title = _extract_between(raw, r"var msg_title = ['\"]([^'\"]+)['\"]")

    author = _extract_between(
        raw, r'id="js_author_name_text"[^>]*>([^<]+)</span>'
    ) or _extract_between(raw, r'id="js_author_name"[^>]*>([^<]+)</span>')

    nickname = _extract_between(raw, r'id="js_name"[^>]*>\s*([^<]+?)\s*</a>')

    frag = _js_content_fragment(raw)
    body_text = _html_to_text(frag) if frag else ""

    print("--- meta ---")
    if title:
        print("title:", title)
    if author:
        print("author:", author)
    if nickname:
        print("account:", nickname)
    print("--- js_content text (may be empty if article is image-only) ---")
    print(body_text or "(no extractable text in js_content)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse WeChat article HTML (meta + visible text in js_content).")
    ap.add_argument("html_file", nargs="?", help="Local HTML file (omit to read stdin)")
    ap.add_argument("--url", help="Fetch article URL (instead of stdin/file)")
    args = ap.parse_args()

    if args.url:
        raw = _fetch_html(args.url)
    else:
        raw = _read_html(args.html_file)

    _emit_meta_and_text(raw)


if __name__ == "__main__":
    main()
