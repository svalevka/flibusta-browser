#!/usr/bin/env python3
"""Local browser-based search UI for the Flibusta library dump.

Usage:
    python3 server.py [--port 8765] [--db flibusta.db] [--library /Volumes/External/fb2.Flibusta.Net]
Then open http://127.0.0.1:8765/ in a browser.
"""
import argparse
import html
import json
import re
import sqlite3
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_DB = str(HERE / "flibusta.db")
DEFAULT_LIBRARY = "/Volumes/External/fb2.Flibusta.Net"

DB_PATH = None
LIBRARY_DIR = None

PAGE_SIZE = 50

ENCODING_RE = re.compile(rb'encoding="([\w\-]+)"', re.IGNORECASE)
ANNOTATION_RE = re.compile(r"<annotation[^>]*>(.*?)</annotation>", re.IGNORECASE | re.DOTALL)
TAG_CLOSE_P_RE = re.compile(r"</p\s*>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


def extract_annotation(fb2_bytes: bytes) -> str | None:
    m = ENCODING_RE.search(fb2_bytes[:200])
    encoding = m.group(1).decode("ascii") if m else "utf-8"
    try:
        text = fb2_bytes.decode(encoding, "replace")
    except LookupError:
        text = fb2_bytes.decode("utf-8", "replace")

    m = ANNOTATION_RE.search(text)
    if not m:
        return None
    block = TAG_CLOSE_P_RE.sub("\n\n", m.group(1))
    block = TAG_RE.sub("", block)
    block = html.unescape(block)
    return re.sub(r"\n{3,}", "\n\n", block).strip() or None


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Flibusta Library</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.3rem; }
  .searchbar { display: flex; flex-wrap: wrap; gap: .5rem; margin-bottom: 1rem; }
  input[type=text] { flex: 1; min-width: 140px; padding: .5rem; font-size: 1rem; }
  select { padding: .5rem; }
  button { padding: .5rem 1rem; cursor: pointer; }
  table { width: 100%; border-collapse: collapse; font-size: .92rem; }
  th, td { text-align: left; padding: .4rem .5rem; border-bottom: 1px solid #8883; vertical-align: top; }
  th { position: sticky; top: 0; background: Canvas; }
  .genre { color: #888; font-size: .85em; }
  .dl { white-space: nowrap; }
  .status { color: #888; margin: .5rem 0; }
  .pager { display: flex; gap: .5rem; align-items: center; margin-top: 1rem; }
  .titlelink { cursor: pointer; color: inherit; text-decoration: underline dotted; }
  .annotation-row td { white-space: pre-wrap; background: #8881; font-size: .88em; }
  .annotation-row .loading, .annotation-row .empty { color: #888; font-style: italic; }
</style>
</head>
<body>
<h1>Flibusta Library Search</h1>
<div class="searchbar">
  <input type="text" id="title" placeholder="Title">
  <input type="text" id="author" placeholder="Author (type to pick)" list="author-list" autocomplete="off">
  <datalist id="author-list"></datalist>
  <select id="series"><option value="">All series</option></select>
  <select id="genre"><option value="">All genres</option></select>
  <select id="sort">
    <option value="relevance">Sort: Relevance</option>
    <option value="title">Sort: Title A-Z</option>
    <option value="author">Sort: Author A-Z</option>
    <option value="date_desc">Sort: Date added (newest)</option>
    <option value="series">Sort: Series order</option>
  </select>
  <label><input type="checkbox" id="has_series"> Series only</label>
  <button onclick="search(0)">Search</button>
</div>
<div class="status" id="status"></div>
<table>
  <thead><tr><th>Author</th><th>Title</th><th>Series</th><th></th></tr></thead>
  <tbody id="results"></tbody>
</table>
<div class="pager">
  <button onclick="prevPage()">&larr; Prev</button>
  <span id="pageinfo"></span>
  <button onclick="nextPage()">Next &rarr;</button>
</div>
<script>
let offset = 0, lastCount = 0;
document.getElementById('title').addEventListener('keydown', e => { if (e.key === 'Enter') search(0); });
document.getElementById('series').addEventListener('change', () => search(0));
document.getElementById('genre').addEventListener('change', () => search(0));
document.getElementById('sort').addEventListener('change', () => search(0));
document.getElementById('has_series').addEventListener('change', () => search(0));

function currentFilterParams() {
  return new URLSearchParams({
    title: document.getElementById('title').value.trim(),
    author: document.getElementById('author').value.trim(),
    series: document.getElementById('series').value.trim(),
    genre: document.getElementById('genre').value,
    has_series: document.getElementById('has_series').checked ? '1' : '',
  });
}

async function loadGenres() {
  const sel = document.getElementById('genre');
  const prev = sel.value;
  const res = await fetch(`/api/genres?${currentFilterParams()}`);
  const data = await res.json();
  sel.innerHTML = '<option value="">All genres</option>';
  let found = false;
  for (const g of data.genres) {
    const opt = document.createElement('option');
    opt.value = g.genre;
    opt.textContent = `${g.genre} (${g.count})`;
    if (g.genre === prev) found = true;
    sel.appendChild(opt);
  }
  sel.value = found ? prev : '';
}

async function loadSeries() {
  const sel = document.getElementById('series');
  const prev = sel.value;
  const res = await fetch(`/api/series?${currentFilterParams()}`);
  const data = await res.json();
  sel.innerHTML = '<option value="">All series</option>';
  let found = false;
  for (const s of data.series) {
    const opt = document.createElement('option');
    opt.value = s.series;
    opt.textContent = `${s.series} (${s.count})`;
    if (s.series === prev) found = true;
    sel.appendChild(opt);
  }
  sel.value = found ? prev : '';
}

function refreshDropdowns() {
  loadGenres();
  loadSeries();
}
refreshDropdowns();

function setupPicker(inputId, listId, endpoint) {
  const input = document.getElementById(inputId);
  const datalist = document.getElementById(listId);
  let timer = null;

  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { datalist.innerHTML = ''; return; }
    timer = setTimeout(async () => {
      const params = currentFilterParams();
      params.set('q', q);
      const res = await fetch(`${endpoint}?${params}`);
      const data = await res.json();
      datalist.innerHTML = '';
      for (const v of data.values) {
        const opt = document.createElement('option');
        opt.value = v;
        datalist.appendChild(opt);
      }
    }, 200);
  });
  // Fires on picking a datalist option or on blur after typing.
  input.addEventListener('change', () => search(0));
  input.addEventListener('keydown', e => { if (e.key === 'Enter') search(0); });
}
setupPicker('author', 'author-list', '/api/authors');

function filterByAuthor(name) {
  document.getElementById('author').value = name;
  search(0);
}

async function search(newOffset) {
  offset = newOffset;
  const sort = document.getElementById('sort').value;
  document.getElementById('status').textContent = 'Searching...';
  const params = currentFilterParams();
  params.set('sort', sort);
  params.set('offset', offset);
  const res = await fetch(`/api/search?${params}`);
  const data = await res.json();
  lastCount = data.results.length;
  const tbody = document.getElementById('results');
  tbody.innerHTML = '';
  for (const b of data.results) {
    const tr = document.createElement('tr');
    const authorLinks = b.author.split('; ')
      .map(a => `<span class="titlelink" onclick="filterByAuthor('${escapeHtml(a)}')">${escapeHtml(a)}</span>`)
      .join('; ');
    tr.innerHTML = `<td>${authorLinks}</td>
      <td><span class="titlelink" onclick="toggleAnnotation(${b.id}, this)">${escapeHtml(b.title)}</span><div class="genre">${escapeHtml(b.genre)}</div></td>
      <td>${escapeHtml(b.series)}${b.serno ? ' #' + escapeHtml(b.serno) : ''}</td>
      <td class="dl"><a href="/api/download?id=${b.id}">.${b.ext}</a></td>`;
    tbody.appendChild(tr);
  }
  document.getElementById('status').textContent = `${data.results.length} result(s)${data.truncated ? ' (showing first page)' : ''}`;
  document.getElementById('pageinfo').textContent = `offset ${offset}`;
  if (newOffset === 0) refreshDropdowns();
}
function nextPage() { if (lastCount > 0) search(offset + lastCount); }
function prevPage() { search(Math.max(0, offset - 50)); }
function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

const annotationCache = {};
async function toggleAnnotation(id, titleEl) {
  const row = titleEl.closest('tr');
  const next = row.nextElementSibling;
  if (next && next.classList.contains('annotation-row')) {
    next.remove();
    return;
  }
  const annoRow = document.createElement('tr');
  annoRow.className = 'annotation-row';
  annoRow.innerHTML = `<td colspan="4" class="loading">Loading annotation...</td>`;
  row.after(annoRow);

  let text = annotationCache[id];
  if (text === undefined) {
    const res = await fetch(`/api/annotation?id=${id}`);
    const data = await res.json();
    text = data.annotation;
    annotationCache[id] = text;
  }
  annoRow.innerHTML = text
    ? `<td colspan="4">${escapeHtml(text)}</td>`
    : `<td colspan="4" class="empty">No annotation available.</td>`;
}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/search":
            self.handle_search(urllib.parse.parse_qs(parsed.query))
        elif parsed.path == "/api/genres":
            self.handle_genres(urllib.parse.parse_qs(parsed.query))
        elif parsed.path == "/api/authors":
            self.handle_authors(urllib.parse.parse_qs(parsed.query))
        elif parsed.path == "/api/series":
            self.handle_series_list(urllib.parse.parse_qs(parsed.query))
        elif parsed.path == "/api/annotation":
            self.handle_annotation(urllib.parse.parse_qs(parsed.query))
        elif parsed.path == "/api/download":
            self.handle_download(urllib.parse.parse_qs(parsed.query))
        else:
            self.send_error(404)

    @staticmethod
    def _fts_terms(text):
        return " ".join(f'"{t}"*' for t in text.split())

    def _build_filters(self, qs, exclude=None):
        """Returns (joins, where, params) for the current filter selection,
        skipping the filter named in `exclude` so dependent dropdowns don't
        restrict themselves by their own current value."""
        def get(name):
            return (qs.get(name, [""])[0]).strip()

        title = get("title")
        author = get("author")
        series = get("series")
        genre = get("genre")
        has_series = get("has_series") == "1"

        joins = []
        where = []
        params = []
        if title and exclude != "title":
            joins.append("JOIN books_fts f ON f.rowid = b.id")
            where.append("books_fts MATCH ?")
            params.append(f"title:{self._fts_terms(title)}")
        if author and exclude != "author":
            joins.append("JOIN book_authors ba ON ba.book_id = b.id")
            where.append("ba.author = ?")
            params.append(author)
        if series and exclude != "series":
            where.append("b.series = ?")
            params.append(series)
        if genre and exclude != "genre":
            joins.append("JOIN book_genres g ON g.book_id = b.id")
            where.append("g.genre = ?")
            params.append(genre)
        if has_series and exclude != "has_series":
            where.append("b.series != ''")
        return joins, where, params

    def handle_genres(self, qs):
        joins, where, params = self._build_filters(qs, exclude="genre")
        sql = "SELECT g.genre, COUNT(DISTINCT b.id) c FROM books b JOIN book_genres g ON g.book_id = b.id " + " ".join(joins)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY g.genre ORDER BY c DESC"
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        self._send_json({"genres": [{"genre": g, "count": c} for g, c in rows]})

    def handle_series_list(self, qs):
        joins, where, params = self._build_filters(qs, exclude="series")
        sql = "SELECT b.series, COUNT(*) c FROM books b " + " ".join(joins)
        where = ["b.series != ''"] + where
        sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY b.series ORDER BY b.series COLLATE NOCASE"
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        self._send_json({"series": [{"series": s, "count": c} for s, c in rows]})

    def handle_authors(self, qs):
        q = (qs.get("q", [""])[0]).strip()
        if len(q) < 2:
            self._send_json({"values": []})
            return
        joins, where, params = self._build_filters(qs, exclude="author")
        sql = "SELECT DISTINCT ba.author FROM books b JOIN book_authors ba ON ba.book_id = b.id " + " ".join(joins)
        where = ["ba.author LIKE ? COLLATE NOCASE"] + where
        params = [f"%{q}%"] + params
        sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ba.author LIMIT 30"
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        self._send_json({"values": [r[0] for r in rows]})

    def handle_search(self, qs):
        def get(name):
            return (qs.get(name, [""])[0]).strip()

        offset = int(get("offset") or 0)
        sort = get("sort")
        order_map = {
            "title": "b.title COLLATE NOCASE ASC",
            "author": "b.author COLLATE NOCASE ASC",
            "date_desc": "b.date DESC",
            "series": "b.series COLLATE NOCASE ASC, CAST(b.serno AS INTEGER) ASC",
        }

        joins, where, params = self._build_filters(qs)
        match_expr = bool(get("title"))

        if sort in order_map:
            order_by = order_map[sort]
        elif match_expr:
            order_by = "rank"
        else:
            order_by = "b.id"

        sql = "SELECT b.id, b.author, b.genre, b.title, b.series, b.serno, b.ext FROM books b " + " ".join(joins)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order_by} LIMIT ? OFFSET ?"
        params += [PAGE_SIZE, offset]

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            self._send_json({"error": str(e), "results": []}, status=400)
            conn.close()
            return
        conn.close()
        results = [dict(r) for r in rows]
        self._send_json({"results": results, "truncated": len(results) == PAGE_SIZE})

    def _load_book_file(self, book_id):
        """Returns (row, data) or (None, error_message)."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT file_id, ext, archive, title FROM books WHERE id = ?", (book_id,)
        ).fetchone()
        conn.close()
        if not row:
            return None, "book not found"

        archive_path = Path(LIBRARY_DIR) / row["archive"]
        inner_name = f"{row['file_id']}.{row['ext']}"
        if not archive_path.exists():
            return None, f"archive missing: {archive_path}"
        try:
            with zipfile.ZipFile(archive_path) as z:
                data = z.read(inner_name)
        except KeyError:
            return None, f"{inner_name} not found in {archive_path.name}"
        return row, data

    def handle_annotation(self, qs):
        ids = qs.get("id", [])
        if not ids:
            self.send_error(400, "missing id")
            return
        row, data = self._load_book_file(ids[0])
        if row is None:
            self._send_json({"annotation": None, "error": data}, status=404)
            return
        annotation = extract_annotation(data) if row["ext"] == "fb2" else None
        self._send_json({"annotation": annotation})

    def handle_download(self, qs):
        ids = qs.get("id", [])
        if not ids:
            self.send_error(400, "missing id")
            return
        row, data = self._load_book_file(ids[0])
        if row is None:
            self.send_error(404, data)
            return

        ascii_title = "".join(c if c.isalnum() or c in " ._-" else "_" for c in row["title"].encode("ascii", "replace").decode("ascii"))[:80].strip("_ ") or row["file_id"]
        ascii_filename = f"{ascii_title}.{row['ext']}"
        utf8_filename = f"{row['title']}.{row['ext']}"
        encoded = urllib.parse.quote(utf8_filename)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{encoded}',
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    global DB_PATH, LIBRARY_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--library", default=DEFAULT_LIBRARY)
    args = ap.parse_args()

    DB_PATH = args.db
    LIBRARY_DIR = args.library

    if not Path(DB_PATH).exists():
        raise SystemExit(f"Index not found at {DB_PATH}. Run build_index.py first.")
    if not Path(LIBRARY_DIR).exists():
        raise SystemExit(f"Library directory not found: {LIBRARY_DIR} (is the external disk mounted?)")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Serving on http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
