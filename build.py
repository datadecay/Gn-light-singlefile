#!/usr/bin/env python3
"""Build a single-file version of GN Light.

Reads a checkout of SCHSwork/gn-light and writes one index.html that has the
game list and every cover thumbnail packed inside, so browsing needs no
network at all. Games themselves still load from the internet when played.

Compared with GN Light it also:
  - sends nothing to any Firebase project (the online counter is removed)
  - drops the service worker and the link back to the full GN 2.0 site
  - works when opened straight from a phone's Files app (storage blocked)
  - fixes Unity games stalling at the end of loading when opened from a file

Usage:
    python3 build.py <path to repo checkout> [output file]
"""
import base64
import io
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "gn-light")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "index.html")
THUMB_SIZE = (180, 135)   # GN Light's are 240x180; this keeps the file ~6 MB
THUMB_QUALITY = 45


def patch(text, old, new, count=1):
    if old not in text:
        sys.exit(f"build.py: couldn't find {old[:70]!r} in GN Light's index.html; it changed")
    return text.replace(old, new, count)


def patch_re(text, pattern, new):
    out, n = re.subn(pattern, new, text, count=1, flags=re.S)
    if n == 0:
        sys.exit(f"build.py: couldn't find /{pattern[:70]}/ in GN Light's index.html; it changed")
    return out


def safe_in_script(text):
    return re.sub(r"</(script)", r"<\\/\1", text, flags=re.I).replace("<!--", "<\\!--")


def thumbs(rows):
    out = {}
    for r in rows:
        gid = str(r[0])
        f = SRC / "t" / f"{gid}.webp"
        if not f.exists():
            continue
        im = Image.open(f).convert("RGB")
        if im.size != THUMB_SIZE:
            im = im.resize(THUMB_SIZE, Image.LANCZOS)
        b = io.BytesIO()
        im.save(b, "WEBP", quality=THUMB_QUALITY, method=6)
        out[gid] = base64.b64encode(b.getvalue()).decode()
    return out


# Runs first in the page: phone Files apps often block storage, and reading it
# throws. GN Light already guards its own reads; this covers everything else.
STORAGE_SHIM = """<script>
(function () {
    function mem() {
        var d = {};
        return { getItem: function (k) { return Object.prototype.hasOwnProperty.call(d, k) ? d[k] : null; },
                 setItem: function (k, v) { d[k] = String(v); }, removeItem: function (k) { delete d[k]; },
                 clear: function () { d = {}; }, key: function (i) { return Object.keys(d)[i] || null; },
                 get length() { return Object.keys(d).length; } };
    }
    ["localStorage", "sessionStorage"].forEach(function (n) {
        try { var s = window[n]; s.setItem("__t", "1"); s.removeItem("__t"); }
        catch (e) { try { Object.defineProperty(window, n, { value: mem(), configurable: true }); window.GNL_NO_SAVE = true; } catch (e2) {} }
    });
})();
</script>
"""

NOSCRIPT = """<noscript><p style="margin:40px 16px;text-align:center;font:16px system-ui,sans-serif">This app can't run games: it has JavaScript turned off. Open the file in a web browser like Chrome, Edge, Firefox or Safari.</p></noscript>
"""

# Added to every game page. Opened from a file, blob: URLs are "blob:null/<id>"
# and older Unity loaders glue them onto the game's folder, so the last download
# 404s and the game stops right before it starts; this puts them back. Also asks
# for CORS on images from hosts that allow it (only when opened from a file),
# for engines that skip it there and then can't draw them.
GAME_FIX = r"""<script>(function(){var re=new RegExp("/(blob:null/[0-9a-f-]{36})([?#]|$)","i");function fix(u){try{var s=String(u&&u.url!==undefined?u.url:u);if(s.indexOf("blob:")===0)return null;var m=s.match(re);if(m)return m[1]}catch(e){}return null}var o=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){var f=fix(u);if(f){var a=[].slice.call(arguments);a[1]=f;return o.apply(this,a)}return o.apply(this,arguments)};if(window.fetch){var F=window.fetch;window.fetch=function(i,n){var f=fix(i);return F.call(this,f||i,n)}}if(self.origin==="null"){var H=new RegExp("^https?://(cdn[.]jsdelivr[.]net|raw[.]githubusercontent[.]com|rawcdn[.]githack[.]com|raw[.]githack[.]com|unpkg[.]com|[^/]+[.]github[.]io)/","i");var d=Object.getOwnPropertyDescriptor(HTMLImageElement.prototype,"src");if(d&&d.set)Object.defineProperty(HTMLImageElement.prototype,"src",{configurable:true,enumerable:d.enumerable,get:d.get,set:function(v){try{if(this.crossOrigin===null&&H.test(new URL(v,document.baseURI).href))this.crossOrigin="anonymous"}catch(e){}d.set.call(this,v)}})}})();</script>"""


def main():
    html = (SRC / "index.html").read_text(encoding="utf-8")
    rows = json.loads((SRC / "games.json").read_text(encoding="utf-8"))
    th = thumbs(rows)

    # ── Remove everything tied to the original site ────────────────────────
    # Online counter: posted to GN 2.0's Firebase every 2 minutes
    html = patch_re(html, r"\n// ── Online count:.*?\n\}\)\(\);\n", "\n")
    # Service worker needs a hosted multi-file site
    html = patch(html, 'if ("serviceWorker" in navigator) addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));\n', "")
    # Link back to the full GN 2.0
    html = patch_re(html, r' <a href="https://schswork\.github\.io/math/">Full GN 2\.0</a> ·', "")
    # games.json is inside the page now; the fallback that rebuilt it from the
    # original repos isn't needed
    html = patch_re(html, r"async function upstreamRows\(\) \{.*?\n\}\n", "")
    html = patch(html, '<link rel="preload" href="games.json" as="fetch" crossorigin>\n', "")

    # ── Game list and thumbnails from inside the page ──────────────────────
    html = patch_re(html,
        r"async function load\(\) \{.*?\n\}\n",
        "async function load() {\n"
        "  // The list and thumbnails are packed into this file\n"
        "  const rows = JSON.parse(document.getElementById(\"gnl-rows\").textContent);\n"
        "  all = rows.map(fromRow); refresh();\n"
        "}\n")
    html = patch(html,
        '  if (z.thumb) return "t/" + z.id + ".webp";',
        '  if (z.thumb && THUMBS[z.id]) return "data:image/webp;base64," + THUMBS[z.id];')
    html = patch(html, "// ── State ──",
        "// Cover thumbnails packed into this file (id → base64 WebP)\n"
        "const THUMBS = JSON.parse(document.getElementById(\"gnl-thumbs\").textContent);\n\n"
        "// ── State ──")

    # ── Games: fixes for files opened from disk / phone Files apps ────────
    html = patch(html,
        "  const d = f.contentDocument; d.open(); d.write(html); d.close();",
        "  html = gnFix(html);\n"
        "  // Locked-down viewers (phone Files apps) can't reach into the frame\n"
        "  const d = f.contentDocument; if (d) { d.open(); d.write(html); d.close(); } else f.srcdoc = html;")
    html = patch(html,
        "try { const h = await gameHtml(cur); w.document.open(); w.document.write(h);",
        "try { const h = gnFix(await gameHtml(cur)); w.document.open(); w.document.write(h);")
    html = patch(html, "let cur = null, frame = $(\"frame\");",
        "const GAME_FIX = " + json.dumps(GAME_FIX).replace("</", "<\\/") + ";\n"
        "function gnFix(html) {\n"
        "  if (!html) return html;\n"
        "  return /<head\\b[^>]*>/i.test(html) ? html.replace(/<head\\b[^>]*>/i, m => m + GAME_FIX) : GAME_FIX + html;\n"
        "}\n"
        "let cur = null, frame = $(\"frame\");")
    # Tell people when this app can't save favorites/recents
    html = patch(html, "load().then(() => {",
        "if (window.GNL_NO_SAVE) setTimeout(() => { $(\"count\").title = \"This app can't save: favorites and recents reset when you close it. Open the file in a browser to keep them.\"; }, 0);\n"
        "load().then(() => {")
    # When saving is blocked, the count line above is rewritten by refresh(); keep the note
    html = patch(html,
        '$("count").textContent = view.length.toLocaleString() + " games";',
        '$("count").textContent = view.length.toLocaleString() + " games" + (window.GNL_NO_SAVE ? " · not saving" : "");')

    # ── Saved settings ─────────────────────────────────────────────────────
    # Chrome gives every file opened from disk the same storage, so settings
    # saved by another copy (or someone's own GN Light-based page) could leak in:
    # a source that isn't in the list filtered out every game ("No games match").
    # Use our own key prefix, and only accept values that make sense.
    html = patch(html, 'localStorage.getItem("gnl-" + k)', 'localStorage.getItem("gnls-" + k)')
    html = patch(html, 'localStorage.setItem("gnl-" + k, JSON.stringify(v))', 'localStorage.setItem("gnls-" + k, JSON.stringify(v))')
    html = patch(html,
        'let favs = new Set(LS.get("favs", [])), recent = LS.get("recent", []);',
        'const arr = v => Array.isArray(v) ? v.map(String) : [];\n'
        'let favs = new Set(arr(LS.get("favs", []))), recent = arr(LS.get("recent", []));')
    html = patch(html,
        'src.value = LS.get("src", "all"); sort.value = LS.get("sort", "az");',
        'src.value = LS.get("src", "all"); sort.value = LS.get("sort", "az");\n'
        'if (!src.value) src.value = "all";\n'
        'if (!sort.value) sort.value = "az";')
    # Say why the list is empty, with a way back to all games
    html = patch(html, '<p id="empty">No games match.</p>',
        '<p id="empty"><span id="emptyText">No games match.</span> <button id="showAll" type="button">Show all games</button></p>')
    html = patch(html,
        '  $("empty").style.display = view.length ? "none" : "block";',
        '  $("empty").style.display = view.length ? "none" : "block";\n'
        '  if (!view.length) $("emptyText").textContent = !all.length ? "The game list didn\'t load." : s ? "No games match \\u201c" + q.value.trim() + "\\u201d." : mode === "fav" ? "No favorites yet. Tap \\u2606 in a game to add one." : mode === "recent" ? "No games played yet." : "No games match.";\n'
        '  $("showAll").hidden = !all.length || (!s && mode === "all");')
    html = patch(html, "load().then(() => {",
        '$("showAll").onclick = () => { q.value = ""; src.value = "all"; LS.set("src", "all"); refresh(); };\n'
        "load().then(() => {")

    # Opened from a file the game frame has no real origin, so "gamepad" (which
    # means "this frame's own origin") doesn't match it and Unity games crash
    # when they look for controllers. "*" allows it wherever the page runs.
    html = patch(html, 'allow="fullscreen; autoplay; gamepad"', 'allow="fullscreen *; autoplay *; gamepad *"')

    # ── Assemble ────────────────────────────────────────────────────────────
    data = ('<script type="application/json" id="gnl-rows">'
            + safe_in_script(json.dumps(rows, separators=(",", ":"), ensure_ascii=False)) + "</script>\n"
            + '<script type="application/json" id="gnl-thumbs">'
            + json.dumps(th, separators=(",", ":")) + "</script>\n")
    html = patch(html, '<meta charset="utf-8">', '<meta charset="utf-8">\n' + STORAGE_SHIM)
    # Data goes before the main script (which is at the end of <body>)
    html = patch(html, '<footer>', NOSCRIPT + data + '<footer>')

    commit = ""
    try:
        commit = subprocess.run(["git", "-C", str(SRC), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        pass
    banner = ("<!-- GN Light, single-file version. Generated by build.py"
              + (f" from GN Light @ {commit}" if commit else "") + ". -->\n")
    html = patch(html, "<!doctype html>\n", "<!doctype html>\n" + banner)

    leftover = [s for s in ("gn-math-dfd85", "AIzaSy", "serviceWorker", "schswork.github.io/math", "games.json\"") if s in html]
    if leftover:
        sys.exit(f"build.py: still contains {leftover}")
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1048576:.1f} MB): {len(rows)} games, {len(th)} thumbnails")


if __name__ == "__main__":
    main()
