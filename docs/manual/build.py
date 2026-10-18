"""
Build the user manual PDFs (English, Tetun, Bahasa Indonesia) from manual.<lang>.md and the
screenshots in img/<lang>/, into static/manual/. Needs Google Chrome and the `websockets` package.

    .venv/bin/python docs/manual/build.py            # all languages
    .venv/bin/python docs/manual/build.py tet        # one language

The Markdown is a small subset: # / ## / ### headings, paragraphs, "1." and "-" lists, pipe tables,
**bold**, and screenshots as a line of their own: ![caption](image-name).
"""
import asyncio
import base64
import datetime
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "..", "static", "manual")
LANGS = {
    "en": {"label": "English", "contents": "Contents", "page": "Page", "updated": "Updated", "file": "QR-Menu-User-Manual-EN.pdf"},
    "tet": {"label": "Tetun", "contents": "Konteúdu", "page": "Pájina", "updated": "Atualiza", "file": "QR-Menu-Manual-Uzuariu-TET.pdf"},
    "id": {"label": "Bahasa Indonesia", "contents": "Daftar isi", "page": "Halaman", "updated": "Diperbarui", "file": "QR-Menu-Panduan-Pengguna-ID.pdf"},
}
PHONE_SHOTS = {"customer_menu", "customer_order", "demo", "phone_dashboard"}
MONTHS = {
    "en": "January February March April May June July August September October November December",
    "tet": "Janeiru Fevereiru Marsu Abríl Maiu Juñu Jullu Agostu Setembru Outubru Novembru Dezembru",
    "id": "Januari Februari Maret April Mei Juni Juli Agustus September Oktober November Desember",
}

CSS = """
@page { size: A4; margin: 16mm 15mm 18mm; }
* { box-sizing: border-box; }
body { font: 10.5pt/1.5 "Noto Sans", "DejaVu Sans", Arial, sans-serif; color: #1f2937; margin: 0; }
.cover { height: 255mm; display: flex; flex-direction: column; justify-content: center; page-break-after: always;
         border-left: 10mm solid #ea580c; padding-left: 14mm; }
.cover .brand { font-size: 15pt; font-weight: 800; color: #ea580c; letter-spacing: .02em; }
.cover h1 { font-size: 32pt; line-height: 1.15; margin: 6mm 0 5mm; color: #111827; }
.cover .lead { font-size: 13pt; color: #4b5563; max-width: 140mm; }
.cover .meta { margin-top: 14mm; color: #6b7280; font-size: 10.5pt; }
.toc { page-break-after: always; }
.toc h2 { border: 0; }
.toc ol { padding-left: 6mm; font-size: 12pt; line-height: 2; }
.toc a { color: #1f2937; text-decoration: none; }
h2 { font-size: 17pt; color: #111827; border-bottom: 2px solid #ea580c; padding-bottom: 2mm; margin: 0 0 4mm;
     page-break-before: always; page-break-after: avoid; }
h3 { font-size: 12.5pt; color: #9a3412; margin: 6mm 0 2mm; page-break-after: avoid; }
p { margin: 0 0 3mm; }
ol, ul { margin: 0 0 3mm; padding-left: 6mm; }
li { margin-bottom: 1.4mm; }
strong { color: #111827; }
table { width: 100%; border-collapse: collapse; margin: 2mm 0 5mm; font-size: 9.5pt; page-break-inside: auto; }
th { background: #fff7ed; text-align: left; border-bottom: 1.5px solid #ea580c; padding: 2mm; }
td { border-bottom: 1px solid #e5e7eb; padding: 1.8mm 2mm; vertical-align: top; }
tr { page-break-inside: avoid; }
td.yes { color: #15803d; font-weight: 700; } td.no { color: #9ca3af; }
figure { margin: 3mm 0 6mm; page-break-inside: avoid; text-align: center; }
figure img { max-width: 100%; max-height: 120mm; border: 1px solid #d1d5db; border-radius: 2mm; }
figure.phone img { max-height: 135mm; max-width: 70mm; }
figcaption { font-size: 9pt; color: #6b7280; margin-top: 1.5mm; }
"""


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def to_html(md: str, lang: str, img_dir: str) -> tuple[str, str, list[str]]:
    """(title, body html, section titles)."""
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", md.strip())]
    title, out, sections = "", [], []
    yes_no = {"Yes", "Sin", "Ya"}, {"No", "Lae", "Tidak"}
    for b in blocks:
        lines = b.split("\n")
        first = lines[0]
        if first.startswith("# "):
            title = first[2:].strip()
        elif first.startswith("## "):
            sections.append(first[3:].strip())
            out.append(f'<h2 id="s{len(sections)}">{inline(first[3:].strip())}</h2>')
        elif first.startswith("### "):
            out.append(f"<h3>{inline(first[4:].strip())}</h3>")
        elif m := re.fullmatch(r"!\[(.*)\]\(([\w-]+)\)", first.strip()):
            caption, name = m.groups()
            path = os.path.join(img_dir, f"{name}.jpg")
            if not os.path.exists(path):
                raise SystemExit(f"missing screenshot {path}")
            cls = ' class="phone"' if name in PHONE_SHOTS else ""
            out.append(f'<figure{cls}><img src="file://{path}" alt=""><figcaption>{inline(caption)}</figcaption></figure>')
        elif first.startswith("|"):
            rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines if not re.fullmatch(r"\|[\s\-|]+\|", ln.strip())]
            head, *body = rows
            t = ["<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"]
            for r in body:
                cells = []
                for c in r:
                    cls = ' class="yes"' if c in yes_no[0] else ' class="no"' if c in yes_no[1] else ""
                    cells.append(f"<td{cls}>{inline(c)}</td>")
                t.append("<tr>" + "".join(cells) + "</tr>")
            out.append("".join(t) + "</tbody></table>")
        elif re.match(r"\d+\. ", first):
            items = [re.sub(r"^\d+\. ", "", ln) for ln in lines]
            out.append("<ol>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ol>")
        elif first.startswith("- "):
            out.append("<ul>" + "".join(f"<li>{inline(ln[2:])}</li>" for ln in lines) + "</ul>")
        elif not sections:
            continue  # the lead paragraph: shown on the cover
        else:
            out.append(f"<p>{inline(' '.join(lines))}</p>")
    return title, "\n".join(out), sections


def page(lang: str) -> str:
    meta = LANGS[lang]
    md = open(os.path.join(HERE, f"manual.{lang}.md"), encoding="utf-8").read()
    title, body, sections = to_html(md, lang, os.path.join(HERE, "img", lang))
    lead = re.split(r"\n\s*\n", md.strip())[1]
    today = datetime.date.today()
    date = f"{today.day} {MONTHS[lang].split()[today.month - 1]} {today.year}"
    toc = "".join(f'<li><a href="#s{i}">{inline(s)}</a></li>' for i, s in enumerate(sections, 1))
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{CSS}</style></head><body>
<section class="cover"><div class="brand">&#9638; QR Menu</div><h1>{inline(title)}</h1>
<p class="lead">{inline(lead)}</p><p class="meta">{meta['label']} · {meta['updated']} {date} · qrmenu.timorstore.com</p></section>
<section class="toc"><h2>{meta['contents']}</h2><ol>{toc}</ol></section>
{body}
</body></html>"""


async def print_pdfs(jobs):
    port = 9351
    prof = tempfile.mkdtemp(prefix="manual-chrome-")
    chrome = subprocess.Popen(["google-chrome", "--headless=new", "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files",
                               f"--remote-debugging-port={port}", f"--user-data-dir={prof}", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        async with websockets.connect(ws_url, max_size=200_000_000) as ws:
            n = 0

            async def cmd(method, **params):
                nonlocal n
                n += 1
                await ws.send(json.dumps({"id": n, "method": method, "params": params}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == n:
                        if "error" in msg:
                            raise RuntimeError(msg["error"])
                        return msg["result"]

            await cmd("Page.enable")
            for lang, html_path, pdf_path in jobs:
                await cmd("Page.navigate", url=f"file://{html_path}")
                await asyncio.sleep(2.5)
                footer = (f'<div style="font-size:8px;color:#9ca3af;width:100%;padding:0 15mm;display:flex;justify-content:space-between">'
                          f'<span>QR Menu · {LANGS[lang]["label"]}</span><span>{LANGS[lang]["page"]} <span class="pageNumber"></span> / '
                          f'<span class="totalPages"></span></span></div>')
                res = await cmd("Page.printToPDF", paperWidth=8.27, paperHeight=11.69, preferCSSPageSize=True, printBackground=True,
                                displayHeaderFooter=True, headerTemplate="<span></span>", footerTemplate=footer)
                open(pdf_path, "wb").write(base64.b64decode(res["data"]))
                print(f"{lang}: {pdf_path} ({os.path.getsize(pdf_path) // 1024} KB)")
    finally:
        chrome.terminate()
        shutil.rmtree(prof, ignore_errors=True)


def main():
    langs = sys.argv[1:] or list(LANGS)
    os.makedirs(OUT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="manual-")
    jobs = []
    for lang in langs:
        path = os.path.join(tmp, f"manual.{lang}.html")
        open(path, "w", encoding="utf-8").write(page(lang))
        jobs.append((lang, path, os.path.abspath(os.path.join(OUT, LANGS[lang]["file"]))))
    asyncio.run(print_pdfs(jobs))
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
