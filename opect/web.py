"""HTTP server for the OPECT literature radar (stdlib only)."""

from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
import signal
import sys
import threading
import traceback
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import __version__, views
from .db import Database
from .sync import SyncEngine, load_config

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONFIG_PATH = ROOT / "config.json"

MIME = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}


def make_handler(db: Database, engine: SyncEngine):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"OPECT-Radar/{__version__}"
        protocol_version = "HTTP/1.1"

        # ------------------------------------------------------------ plumbing
        def log_message(self, fmt: str, *args) -> None:  # quieter logs
            if os.environ.get("OPECT_DEBUG"):
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _html(self, text: str, status: int = 200) -> None:
            self._send(status, text.encode("utf-8"), "text/html; charset=utf-8")

        def _json(self, obj: Any, status: int = 200) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _error(self, status: int, message: str) -> None:
            page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{status}</title>
            <link rel="stylesheet" href="/static/style.css"></head><body class="error-body">
            <div class="error-card"><h1>{status}</h1><p>{message}</p><a class="btn btn-primary" href="/">返回首页</a></div>
            </body></html>"""
            self._html(page, status)

        def _params(self) -> dict[str, Any]:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query, keep_blank_values=False)
            return {k: v[0] for k, v in qs.items()}, parsed.path

        # ----------------------------------------------------------------- GET
        def do_GET(self) -> None:  # noqa: N802
            try:
                params, path = self._params()
                if path.startswith("/static/"):
                    return self._static(path)
                if path == "/":
                    return self._html(views.home(db, engine))
                if path == "/papers":
                    return self._html(views.papers_page(db, params))
                if path.startswith("/paper/"):
                    try:
                        wid = int(path.split("/")[2])
                    except (ValueError, IndexError):
                        return self._error(404, "文献不存在")
                    page = views.paper_page(db, wid)
                    return self._html(page) if page else self._error(404, "文献不存在")
                if path == "/timeline":
                    return self._html(views.timeline_page(db, params))
                if path == "/stats":
                    return self._html(views.stats_page(db))
                if path == "/settings":
                    return self._html(views.settings_page(db, engine, params.get("saved", "")))
                if path == "/api/sync/status":
                    return self._json({"ok": True, "state": engine.state.as_dict(), "stats": db.stats()})
                if path == "/api/search":
                    q = (params.get("q") or "").strip()
                    if len(q) < 2:
                        return self._json({"ok": True, "items": []})
                    res = db.query_works(q=q, per_page=8, sort="relevance")
                    items = [
                        {
                            "id": w["id"],
                            "title": w["title"],
                            "venue": w.get("venue"),
                            "date": w.get("published_date"),
                            "relevance": w.get("relevance"),
                        }
                        for w in res["items"]
                    ]
                    return self._json({"ok": True, "items": items, "total": res["total"]})
                if path.startswith("/export."):
                    return self._export(path.split(".")[-1], params)
                if path == "/healthz":
                    return self._json({"ok": True, "version": __version__, "works": db.stats()["total"]})
                return self._error(404, "页面不存在")
            except BrokenPipeError:
                return
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                self._error(500, "服务器内部错误，请查看控制台日志。")

        # ---------------------------------------------------------------- POST
        def do_POST(self) -> None:  # noqa: N802
            try:
                params, path = self._params()
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                ctype = self.headers.get("Content-Type", "")
                if "application/json" in ctype:
                    payload = json.loads(raw or b"{}")
                else:
                    payload = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}

                if path == "/api/sync":
                    return self._json(engine.start("manual"))
                if path == "/api/sync/stop":
                    engine.stop()
                    return self._json({"ok": True})
                if path == "/api/favorite":
                    wid = int(payload.get("id") or 0)
                    value = 1 if str(payload.get("value", "1")) in ("1", "true", "on") else 0
                    ok = db.set_flag(wid, "favorite", value)
                    return self._json({"ok": ok, "favorite": bool(value)})
                if path == "/api/read":
                    wid = int(payload.get("id") or 0)
                    status = payload.get("status") or "read"
                    ok = db.set_flag(wid, "read_status", status)
                    return self._json({"ok": ok})
                if path == "/api/note":
                    wid = int(payload.get("id") or 0)
                    note = (payload.get("note") or "").strip()[:8000]
                    ok = db.set_flag(wid, "note", note)
                    return self._json({"ok": ok})
                if path == "/api/rescore":
                    result = db.rescore_all(
                        engine.scorer,
                        prune_below=float(engine.config.get("relevance", {}).get("include_threshold", 30)),
                    )
                    return self._json({"ok": True, "result": result})
                if path == "/api/clear":
                    if payload.get("confirm") != "DELETE":
                        return self._json({"ok": False, "message": "需要确认"}, 400)
                    with db.connect() as conn:
                        conn.execute("DELETE FROM work_authors")
                        conn.execute("DELETE FROM authors")
                        conn.execute("DELETE FROM works")
                        if db.fts_enabled:
                            conn.execute("DELETE FROM works_fts")
                    return self._json({"ok": True})
                if path == "/settings":
                    return self._save_settings(payload)
                return self._error(404, "接口不存在")
            except BrokenPipeError:
                return
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                self._json({"ok": False, "message": "服务器内部错误"}, 500)

        # ------------------------------------------------------------- helpers
        def _static(self, path: str) -> None:
            rel = unquote(path[len("/static/") :])
            target = (STATIC / rel).resolve()
            if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
                return self._error(404, "资源不存在")
            ctype = MIME.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            body = target.read_bytes()
            self._send(200, body, ctype, {"Cache-Control": "public, max-age=300"})

        def _save_settings(self, payload: dict[str, Any]) -> None:
            cfg = load_config()
            sync = cfg.setdefault("sync", {})
            rel = cfg.setdefault("relevance", {})
            form = payload.get("form") or "sync"
            try:
                if form == "relevance":
                    if "include_threshold" in payload:
                        rel["include_threshold"] = max(0, min(100, float(payload["include_threshold"])))
                    if "core_threshold" in payload:
                        rel["core_threshold"] = max(0, min(100, float(payload["core_threshold"])))
                else:
                    if "interval_minutes" in payload:
                        sync["interval_minutes"] = max(15, min(10080, int(payload["interval_minutes"])))
                    if "initial_lookback_days" in payload:
                        sync["initial_lookback_days"] = max(30, min(7300, int(payload["initial_lookback_days"])))
                    if "contact_email" in payload:
                        sync["contact_email"] = str(payload["contact_email"]).strip()[:120]
                    sync["auto_sync"] = "auto_sync" in payload
                    sync["on_start"] = "on_start" in payload
                    sources = sync.setdefault("sources", {})
                    for key in ("openalex", "crossref", "pubmed", "arxiv"):
                        sources[key] = f"source_{key}" in payload
            except (TypeError, ValueError):
                return self._error(400, "设置值无效")
            tmp = CONFIG_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(CONFIG_PATH)
            engine.config = cfg
            engine.scorer = type(engine.scorer)(cfg)
            self._redirect("/settings?saved=" + quote("设置已保存"))
            return None

        def _export(self, fmt: str, params: dict[str, Any]) -> None:
            fmt = fmt.lower()
            if fmt not in ("csv", "bib", "json"):
                return self._error(404, "不支持的导出格式")
            rows = list(db.all_works_for_export())
            stamp = datetime.now().strftime("%Y%m%d")
            if fmt == "csv":
                buf = io.StringIO()
                writer = csv.writer(buf)
                writer.writerow(["title", "authors", "venue", "published_date", "doi", "relevance", "tier", "topics", "cited_by", "url"])
                for w in rows:
                    writer.writerow([
                        w["title"],
                        "; ".join(a.get("name", "") for a in w.get("authors") or []),
                        w.get("venue") or "",
                        w.get("published_date") or "",
                        w.get("doi") or "",
                        w.get("relevance"),
                        w.get("tier"),
                        ", ".join(w.get("topics") or []),
                        w.get("cited_by") or 0,
                        f"https://doi.org/{w['doi']}" if w.get("doi") else (w.get("landing_url") or ""),
                    ])
                body = buf.getvalue().encode("utf-8-sig")
                return self._send(
                    200, body, "text/csv; charset=utf-8",
                    {"Content-Disposition": f'attachment; filename="opect-literature-{stamp}.csv"'},
                )
            if fmt == "json":
                body = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
                return self._send(
                    200, body, "application/json; charset=utf-8",
                    {"Content-Disposition": f'attachment; filename="opect-literature-{stamp}.json"'},
                )
            # BibTeX
            entries = []
            used: set[str] = set()
            for w in rows:
                first = ""
                for a in w.get("authors") or []:
                    name = (a.get("name") or "").strip()
                    if name:
                        first = name.split()[-1]
                        break
                year = w.get("published_year") or "n.d."
                key = "".join(ch for ch in f"{first}{year}" if ch.isalnum()) or "opect"
                base, i = key, 1
                while key in used:
                    i += 1
                    key = f"{base}{i}"
                used.add(key)
                authors = " and ".join(a.get("name", "") for a in w.get("authors") or []) or "Unknown"
                fields = {
                    "title": w["title"],
                    "author": authors,
                    "journal": w.get("venue") or "",
                    "year": str(year),
                    "doi": w.get("doi") or "",
                    "url": f"https://doi.org/{w['doi']}" if w.get("doi") else (w.get("landing_url") or ""),
                    "keywords": ", ".join(w.get("topics") or []),
                    "note": f"OPECT relevance {w.get('relevance')}",
                }
                body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v)
                entries.append(f"@article{{{key},\n{body}\n}}")
            text = "\n\n".join(entries).encode("utf-8")
            return self._send(
                200, text, "application/x-bibtex; charset=utf-8",
                {"Content-Disposition": f'attachment; filename="opect-literature-{stamp}.bib"'},
            )

        # HEAD support (some uptime checkers use it)
        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

    return Handler


class QuietServer(ThreadingHTTPServer):
    """Threaded server that ignores dropped client connections (browsers, tunnels)."""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def run_server(host: str, port: int, db_path: str, auto: bool = True) -> None:
    cfg = load_config()
    db = Database(db_path)
    engine = SyncEngine(db, cfg)
    handler = make_handler(db, engine)
    httpd = QuietServer((host, port), handler)
    if auto:
        threading.Thread(target=engine.auto_loop, daemon=True, name="auto-sync").start()
    print(f"\n  OPECT 文献雷达 v{__version__}")
    print(f"  → http://{host}:{port}")
    print(f"  → 数据库: {db_path}")
    print(f"  → 已收录 {db.stats()['total']} 篇相关文献\n")
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        engine.stop()
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OPECT literature radar")
    parser.add_argument("--host", default=os.environ.get("OPECT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OPECT_PORT", "8765")))
    parser.add_argument("--db", default=os.environ.get("OPECT_DB", str(ROOT / "data" / "opect.db")))
    parser.add_argument("--no-auto", action="store_true", help="disable background auto-sync")
    args = parser.parse_args(argv)
    run_server(args.host, args.port, args.db, auto=not args.no_auto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
