import os
import re
import io
import ipaddress
import socket
import zipfile
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag, unquote
from html.parser import HTMLParser

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MAX_PAGES = 20
MAX_FILES = 250
MAX_TOTAL_BYTES = 35 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
TIMEOUT = 15
UA = "PublicSiteAssetCollector/1.0 (+downloads publicly served page assets)"

class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.assets = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag in ("script", "img", "source", "iframe", "video", "audio") and a.get("src"):
            self.assets.append(a["src"])
        if tag == "link" and a.get("href"):
            rel = (a.get("rel") or "").lower()
            if any(x in rel for x in ("stylesheet", "icon", "preload", "modulepreload")):
                self.assets.append(a["href"])
        if tag == "video" and a.get("poster"):
            self.assets.append(a["poster"])

def safe_public_url(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    if p.username or p.password:
        return False
    host = p.hostname.lower()
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".local"):
        return False
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80))
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                return False
    except Exception:
        return False
    return True

def clean_url(url):
    return urldefrag(url)[0]

def safe_name(url, content_type=""):
    p = urlparse(url)
    name = unquote(p.path.rsplit("/", 1)[-1]) or "index"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:100]
    if "." not in name:
        c = content_type.lower()
        ext = ".css" if "css" in c else ".js" if "javascript" in c else ".html" if "html" in c else ".bin"
        name += ext
    host = (p.hostname or "site").replace(".", "_")
    folder = re.sub(r"[^A-Za-z0-9_-]", "_", host)
    return f"{folder}/{p.path.strip('/') or 'index'}" if p.path.strip("/") else f"{folder}/index.html"

def fetch(url, session):
    if not safe_public_url(url):
        raise ValueError("Blocked non-public or unsupported URL.")
    r = session.get(url, timeout=TIMEOUT, allow_redirects=False, stream=True)
    if r.is_redirect:
        target = urljoin(url, r.headers.get("Location", ""))
        r.close()
        if not safe_public_url(target):
            raise ValueError("Redirect target is not a public HTTP(S) URL.")
        r = session.get(target, timeout=TIMEOUT, allow_redirects=False, stream=True)
    r.raise_for_status()
    chunks, size = [], 0
    for chunk in r.iter_content(65536):
        if not chunk:
            continue
        size += len(chunk)
        if size > MAX_FILE_BYTES:
            raise ValueError("File exceeds per-file size limit.")
        chunks.append(chunk)
    data = b"".join(chunks)
    return r.url, r.headers.get("Content-Type", ""), data

def collect(start_url):
    if not safe_public_url(start_url):
        raise ValueError("Please send a public http:// or https:// website URL.")
    start = clean_url(start_url)
    origin = (urlparse(start).scheme, urlparse(start).netloc.lower())
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    queue = deque([start])
    seen_pages, seen_assets, saved = set(), set(), {}
    total = 0

    while queue and len(seen_pages) < MAX_PAGES and len(saved) < MAX_FILES:
        page_url = clean_url(queue.popleft())
        if page_url in seen_pages:
            continue
        p = urlparse(page_url)
        if (p.scheme, p.netloc.lower()) != origin:
            continue
        seen_pages.add(page_url)
        try:
            final_url, ctype, data = fetch(page_url, session)
            if "html" not in ctype.lower() and not data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                continue
            rel = safe_name(final_url, "text/html")
            if rel in saved:
                rel = rel.rsplit("/", 1)[0] + "/" + str(len(saved)) + "_page.html"
            saved[rel] = data
            total += len(data)
            parser = AssetParser()
            parser.feed(data.decode("utf-8", errors="replace"))
            for ref in parser.assets:
                asset_url = clean_url(urljoin(final_url, ref.strip()))
                if asset_url not in seen_assets:
                    seen_assets.add(asset_url)
            for ref in parser.links:
                nxt = clean_url(urljoin(final_url, ref.strip()))
                np = urlparse(nxt)
                if (np.scheme, np.netloc.lower()) == origin and nxt not in seen_pages:
                    queue.append(nxt)
        except Exception:
            continue

    # Download referenced public assets, including CDN-hosted files, without bypassing access controls.
    pending = list(seen_assets)
    css_seen = set()
    while pending and len(saved) < MAX_FILES and total < MAX_TOTAL_BYTES:
        asset_url = pending.pop(0)
        if asset_url in css_seen:
            continue
        css_seen.add(asset_url)
        try:
            final_url, ctype, data = fetch(asset_url, session)
            if total + len(data) > MAX_TOTAL_BYTES:
                continue
            rel = safe_name(final_url, ctype)
            base, ext = rel.rsplit("/", 1) if "/" in rel else ("assets", rel)
            rel = f"assets/{base}_{ext}"
            suffix = 1
            original = rel
            while rel in saved:
                rel = original.rsplit(".", 1)[0] + f"_{suffix}." + original.rsplit(".", 1)[-1]
                suffix += 1
            saved[rel] = data
            total += len(data)
            if "css" in ctype.lower() or final_url.lower().endswith(".css"):
                text = data.decode("utf-8", errors="replace")
                for ref in re.findall(r'url\(["\\']?([^)"\\']+)', text):
                    if not ref.startswith("data:"):
                        pending.append(clean_url(urljoin(final_url, ref.strip())))
                for ref in re.findall(r'@import\s+["\\']([^"\\']+)', text):
                    pending.append(clean_url(urljoin(final_url, ref.strip())))
        except Exception:
            continue

    return saved, len(seen_pages), total

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send a public website URL. I’ll package its publicly served HTML pages and linked CSS, "
        "JavaScript, images, fonts and other static assets into a ZIP.\\n\\n"
        "Limits: up to 20 pages, 250 files, 35 MB total. I can’t retrieve server-side code, "
        "private files, protected content, or anything the site does not publicly serve."
    )

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    match = re.search(r'https?://\S+', text)
    if not match:
        await update.message.reply_text("Please send a full URL starting with http:// or https://")
        return
    url = match.group(0).rstrip(".,)")
    status = await update.message.reply_text("Collecting publicly available site files…")
    try:
        files, pages, total = collect(url)
        if not files:
            await status.edit_text("I couldn’t collect any public HTML/assets from that URL.")
            return
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, data)
            note = (
                "Collected public browser-delivered files only. This ZIP is not the original "
                "server project and may not run as a complete local copy. Server-side code, "
                "databases, secrets, private endpoints and protected files are not included.\\n"
                f"Pages visited: {pages}\\nFiles saved: {len(files)}\\nBytes: {total}\\n"
            )
            z.writestr("COLLECTION_NOTES.txt", note)
        mem.seek(0)
        filename = (urlparse(url).hostname or "website").replace(".", "_") + "_public_assets.zip"
        await update.message.reply_document(document=mem, filename=filename,
            caption=f"Done: {len(files)} public files collected from {pages} page(s).")
        await status.delete()
    except Exception as e:
        await status.edit_text(f"Could not collect this URL: {str(e)[:250]}")

def main():
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN environment variable first.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.run_polling()

if __name__ == "__main__":
    main()
