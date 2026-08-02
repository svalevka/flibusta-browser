#!/usr/bin/env python3
"""Build a local SQLite search index from the Flibusta .inpx catalog.

Usage:
    python3 build_index.py [--inpx PATH] [--db PATH]
"""
import argparse
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

DEFAULT_INPX = "/Volumes/External/fb2.Flibusta.Net/flibusta_fb2_local.inpx"
DEFAULT_DB = str(Path(__file__).parent / "flibusta.db")


def clean_author_name(raw: str) -> str:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return " ".join(parts)


def parse_inp(data: bytes, archive: str):
    for line in data.split(b"\r\n"):
        if not line:
            continue
        f = line.split(b"\x04")
        if len(f) < 12:
            continue
        dec = lambda i: f[i].decode("utf-8", "replace") if i < len(f) else ""
        authors = [clean_author_name(a) for a in dec(0).strip(":").split(":")]
        authors = [a for a in authors if a]
        author = "; ".join(authors)
        genre = dec(1).strip(":").replace(":", ", ")
        title = dec(2).strip()
        series = dec(3).strip()
        serno = dec(4)
        file_id = dec(5)
        size = dec(6)
        ext = dec(9)
        date = dec(10)
        lang = dec(11)
        if not title or not file_id:
            continue
        yield (author, genre, title, series, serno, file_id, size, ext, date, lang, archive)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inpx", default=DEFAULT_INPX)
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()

    inpx_path = Path(args.inpx)
    if not inpx_path.exists():
        sys.exit(f"inpx not found: {inpx_path}")

    db_path = Path(args.db)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            author TEXT,
            genre TEXT,
            title TEXT,
            series TEXT,
            serno TEXT,
            file_id TEXT,
            size INTEGER,
            ext TEXT,
            date TEXT,
            lang TEXT,
            archive TEXT
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE books_fts USING fts5(
            author, title, series, genre, content='books', content_rowid='id',
            tokenize='unicode61'
        )
    """)
    conn.execute("""
        CREATE TRIGGER books_ai AFTER INSERT ON books BEGIN
            INSERT INTO books_fts(rowid, author, title, series, genre)
            VALUES (new.id, new.author, new.title, new.series, new.genre);
        END
    """)
    conn.execute("""
        CREATE TABLE book_genres (
            book_id INTEGER NOT NULL,
            genre TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE book_authors (
            book_id INTEGER NOT NULL,
            author TEXT NOT NULL
        )
    """)

    t0 = time.time()
    total = 0
    with zipfile.ZipFile(inpx_path) as z:
        names = [n for n in z.namelist() if n.endswith(".inp")]
        print(f"Found {len(names)} archive index files")
        for i, name in enumerate(names, 1):
            archive = name[:-4] + ".zip"
            data = z.read(name)
            rows = list(parse_inp(data, archive))
            conn.executemany(
                "INSERT INTO books (author, genre, title, series, serno, file_id, size, ext, date, lang, archive) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            total += len(rows)
            if i % 20 == 0 or i == len(names):
                print(f"  [{i}/{len(names)}] {name}: {len(rows)} books (total {total})")

    conn.execute("CREATE INDEX idx_author ON books(author)")
    conn.execute("CREATE INDEX idx_archive ON books(archive)")
    conn.execute("CREATE INDEX idx_series ON books(series)")

    print("Building genre index...")
    genre_rows = []
    for book_id, genre in conn.execute("SELECT id, genre FROM books WHERE genre != ''"):
        for g in genre.split(", "):
            g = g.strip()
            if g:
                genre_rows.append((book_id, g))
    conn.executemany("INSERT INTO book_genres (book_id, genre) VALUES (?, ?)", genre_rows)
    conn.execute("CREATE INDEX idx_book_genres_genre ON book_genres(genre)")
    conn.execute("CREATE INDEX idx_book_genres_book ON book_genres(book_id)")

    print("Building author index...")
    author_rows = []
    for book_id, author in conn.execute("SELECT id, author FROM books WHERE author != ''"):
        for a in author.split("; "):
            a = a.strip()
            if a:
                author_rows.append((book_id, a))
    conn.executemany("INSERT INTO book_authors (book_id, author) VALUES (?, ?)", author_rows)
    conn.execute("CREATE INDEX idx_book_authors_author ON book_authors(author)")
    conn.execute("CREATE INDEX idx_book_authors_book ON book_authors(book_id)")

    conn.commit()
    conn.close()
    print(f"Done: {total} books indexed in {time.time()-t0:.1f}s -> {db_path}")


if __name__ == "__main__":
    main()
