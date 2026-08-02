# flibusta-browser

A local, browser-based search UI for a Flibusta library dump (the `.inpx` catalog
plus the numbered `.zip` archives of `.fb2` books, e.g. from `fb2.Flibusta.Net`).

It builds a local SQLite full-text index from the `.inpx` catalog, then serves a
small dependency-free web UI to search/filter by title, author, series, and genre
(with cascading dropdowns), view book annotations, and download the matching
`.fb2` extracted on demand from the correct zip — without ever unpacking the
whole library.

## Requirements

- Python 3.9+ (standard library only, no `pip install` needed)
- The Flibusta dump itself: a `flibusta_fb2_local.inpx` file and the `fb2-*.zip`
  archives it indexes, all in one directory (e.g. on an external drive)

## 1. Build the index

```bash
python3 build_index.py --inpx /path/to/flibusta_fb2_local.inpx
```

This creates `flibusta.db` next to the script (takes well under a minute). Run it
again any time the `.inpx` catalog is updated. Defaults to
`/Volumes/External/fb2.Flibusta.Net/flibusta_fb2_local.inpx` if `--inpx` is omitted.

## 2. Start the server

```bash
python3 server.py --library /path/to/fb2.Flibusta.Net
```

Then open **http://127.0.0.1:8765/** in your browser.

`--library` should point at the directory containing the `fb2-*.zip` archives
(defaults to `/Volumes/External/fb2.Flibusta.Net`). Search works even if this
directory isn't mounted; downloads and annotations need it available.

### Options

```
python3 server.py [--port 8765] [--db flibusta.db] [--library /path/to/fb2.Flibusta.Net]
```

## Notes

- `flibusta.db` is generated locally and gitignored — regenerate it with
  `build_index.py` after cloning.
- To stop the server, `Ctrl+C` the foreground process, or if it was started
  with `nohup ... &`, find and kill it: `pkill -f "python3 server.py"`.
