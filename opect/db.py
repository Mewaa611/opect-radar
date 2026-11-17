"""SQLite storage for the OPECT literature tracker."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .scoring import normalize

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS works (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    doi               TEXT UNIQUE,
    normalized_title  TEXT UNIQUE NOT NULL,
    title             TEXT NOT NULL,
    abstract          TEXT,
    authors_json      TEXT NOT NULL DEFAULT '[]',
    venue             TEXT,
    publisher         TEXT,
    work_type         TEXT,
    published_date    TEXT,
    published_year    INTEGER,
    cited_by          INTEGER DEFAULT 0,
    is_open_access    INTEGER DEFAULT 0,
    oa_url            TEXT,
    landing_url       TEXT,
    pdf_url           TEXT,
    topics_json       TEXT NOT NULL DEFAULT '[]',
    keywords_json     TEXT NOT NULL DEFAULT '[]',
    relevance         REAL NOT NULL DEFAULT 0,
    tier              TEXT NOT NULL DEFAULT 'excluded',
    relevance_reason  TEXT,
    hits_json         TEXT NOT NULL DEFAULT '[]',
    sources_json      TEXT NOT NULL DEFAULT '[]',
    source_ids_json   TEXT NOT NULL DEFAULT '{}',
    favorite          INTEGER NOT NULL DEFAULT 0,
    read_status       TEXT NOT NULL DEFAULT 'unread',
    note              TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_works_published ON works(published_date DESC);
CREATE INDEX IF NOT EXISTS idx_works_relevance ON works(relevance DESC);
CREATE INDEX IF NOT EXISTS idx_works_tier ON works(tier);
CREATE INDEX IF NOT EXISTS idx_works_venue ON works(venue);
CREATE INDEX IF NOT EXISTS idx_works_year ON works(published_year);

CREATE TABLE IF NOT EXISTS authors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    norm_name  TEXT NOT NULL UNIQUE,
    orcid      TEXT,
    works_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS work_authors (
    work_id    INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    author_id  INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (work_id, author_id)
);
CREATE INDEX IF NOT EXISTS idx_wa_author ON work_authors(author_id);

CREATE TABLE IF NOT EXISTS sync_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    trigger       TEXT NOT NULL DEFAULT 'manual',
    stats_json    TEXT NOT NULL DEFAULT '{}',
    message       TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS works_fts USING fts5(
    title, abstract, authors, venue, keywords
);
"""

FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS works_ai AFTER INSERT ON works BEGIN
  INSERT INTO works_fts(rowid, title, abstract, authors, venue, keywords)
  VALUES (new.id, new.title, coalesce(new.abstract,''), coalesce(new.authors_json,''),
          coalesce(new.venue,''), coalesce(new.keywords_json,''));
END;
CREATE TRIGGER IF NOT EXISTS works_ad AFTER DELETE ON works BEGIN
  DELETE FROM works_fts WHERE rowid = old.id;
END;
CREATE TRIGGER IF NOT EXISTS works_au AFTER UPDATE ON works BEGIN
  DELETE FROM works_fts WHERE rowid = old.id;
  INSERT INTO works_fts(rowid, title, abstract, authors, venue, keywords)
  VALUES (new.id, new.title, coalesce(new.abstract,''), coalesce(new.authors_json,''),
          coalesce(new.venue,''), coalesce(new.keywords_json,''));
END;
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value[: len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"(\d{4})$", value)
    if m:
        return date(int(m.group(1)), 1, 1)
    return None


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.fts_enabled = True
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            try:
                conn.executescript(FTS_TRIGGERS)
            except sqlite3.OperationalError:
                self.fts_enabled = False
            self._migrate_fts(conn)
            self._backfill_fts(conn)

    def _migrate_fts(self, conn: sqlite3.Connection) -> None:
        """Older builds used a contentless FTS table, which cannot delete rows."""
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='works_fts'"
            ).fetchone()
            if row and "content=''" in (row["sql"] or "").replace(" ", ""):
                conn.executescript(
                    "DROP TRIGGER IF EXISTS works_ai;"
                    "DROP TRIGGER IF EXISTS works_ad;"
                    "DROP TRIGGER IF EXISTS works_au;"
                    "DROP TABLE IF EXISTS works_fts;"
                )
                conn.executescript(SCHEMA)
                conn.executescript(FTS_TRIGGERS)
        except sqlite3.OperationalError:
            self.fts_enabled = False

    # ------------------------------------------------------------------ conn
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _backfill_fts(self, conn: sqlite3.Connection) -> None:
        if not self.fts_enabled:
            return
        try:
            have = conn.execute("SELECT count(*) FROM works_fts").fetchone()[0]
            total = conn.execute("SELECT count(*) FROM works").fetchone()[0]
            if have != total:
                conn.execute("DELETE FROM works_fts")
                conn.execute(
                    """INSERT INTO works_fts(rowid, title, abstract, authors, venue, keywords)
                       SELECT id, title, coalesce(abstract,''), coalesce(authors_json,''),
                              coalesce(venue,''), coalesce(keywords_json,'') FROM works"""
                )
                conn.commit()
        except sqlite3.OperationalError:
            self.fts_enabled = False

    # ---------------------------------------------------------------- upsert
    def find_existing(self, doi: str | None, normalized_title: str) -> sqlite3.Row | None:
        conn = self._conn()
        if doi:
            row = conn.execute("SELECT * FROM works WHERE doi = ?", (doi.lower(),)).fetchone()
            if row:
                return row
        return conn.execute(
            "SELECT * FROM works WHERE normalized_title = ?", (normalized_title,)
        ).fetchone()

    def upsert_work(self, record: dict[str, Any], *, now: str | None = None) -> tuple[int, bool]:
        """Insert or merge a work. Returns (work_id, created)."""
        now = now or utcnow()
        conn = self._conn()
        doi = (record.get("doi") or "").lower() or None
        ntitle = record["normalized_title"]
        existing = self.find_existing(doi, ntitle)

        authors = record.get("authors") or []
        topics = record.get("topics") or []
        keywords = record.get("keywords") or []
        hits = record.get("hits") or []
        sources = record.get("sources") or []
        source_ids = record.get("source_ids") or {}

        if existing:
            old_sources = set(json.loads(existing["sources_json"] or "[]"))
            new_sources = sorted(old_sources | set(sources))
            old_ids = json.loads(existing["source_ids_json"] or "{}")
            old_ids.update(source_ids)
            # Prefer richer values, never overwrite with blanks.
            def better(new, old):
                return new if (new not in (None, "", 0, "[]", "{}") and not old) else old

            title = existing["title"] or record["title"]
            abstract = existing["abstract"] or record.get("abstract")
            if record.get("abstract") and existing["abstract"]:
                if len(record["abstract"]) > len(existing["abstract"]) * 1.2:
                    abstract = record["abstract"]
            merged = {
                "doi": existing["doi"] or doi,
                "title": title,
                "abstract": abstract,
                "authors_json": existing["authors_json"]
                if len(json.loads(existing["authors_json"] or "[]")) >= len(authors)
                else json.dumps(authors, ensure_ascii=False),
                "venue": better(record.get("venue"), existing["venue"]),
                "publisher": better(record.get("publisher"), existing["publisher"]),
                "work_type": better(record.get("work_type"), existing["work_type"]),
                "published_date": existing["published_date"] or record.get("published_date"),
                "published_year": existing["published_year"] or record.get("published_year"),
                "cited_by": max(int(existing["cited_by"] or 0), int(record.get("cited_by") or 0)),
                "is_open_access": max(int(existing["is_open_access"] or 0), int(record.get("is_open_access") or 0)),
                "oa_url": better(record.get("oa_url"), existing["oa_url"]),
                "landing_url": better(record.get("landing_url"), existing["landing_url"]),
                "pdf_url": better(record.get("pdf_url"), existing["pdf_url"]),
                "topics_json": json.dumps(
                    sorted(set(json.loads(existing["topics_json"] or "[]")) | set(topics)),
                    ensure_ascii=False,
                ),
                "keywords_json": existing["keywords_json"]
                if len(json.loads(existing["keywords_json"] or "[]")) >= len(keywords)
                else json.dumps(keywords, ensure_ascii=False),
                "relevance": max(float(existing["relevance"] or 0), float(record.get("relevance") or 0)),
                "tier": existing["tier"]
                if float(existing["relevance"] or 0) >= float(record.get("relevance") or 0)
                else record.get("tier", "excluded"),
                "relevance_reason": better(record.get("relevance_reason"), existing["relevance_reason"]),
                "hits_json": existing["hits_json"]
                if len(json.loads(existing["hits_json"] or "[]")) >= len(hits)
                else json.dumps(hits, ensure_ascii=False),
                "sources_json": json.dumps(new_sources, ensure_ascii=False),
                "source_ids_json": json.dumps(old_ids, ensure_ascii=False),
                "last_seen_at": now,
                "updated_at": now,
                "id": existing["id"],
            }
            sets = ", ".join(f"{k} = :{k}" for k in merged if k != "id")
            conn.execute(f"UPDATE works SET {sets} WHERE id = :id", merged)
            work_id = existing["id"]
            self._sync_authors(work_id, json.loads(merged["authors_json"] or "[]"))
            return work_id, False

        cur = conn.execute(
            """INSERT INTO works (
                doi, normalized_title, title, abstract, authors_json, venue, publisher,
                work_type, published_date, published_year, cited_by, is_open_access,
                oa_url, landing_url, pdf_url, topics_json, keywords_json, relevance, tier,
                relevance_reason, hits_json, sources_json, source_ids_json,
                first_seen_at, last_seen_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                doi, ntitle, record["title"], record.get("abstract"),
                json.dumps(authors, ensure_ascii=False), record.get("venue"),
                record.get("publisher"), record.get("work_type"),
                record.get("published_date"), record.get("published_year"),
                int(record.get("cited_by") or 0), int(record.get("is_open_access") or 0),
                record.get("oa_url"), record.get("landing_url"), record.get("pdf_url"),
                json.dumps(topics, ensure_ascii=False), json.dumps(keywords, ensure_ascii=False),
                float(record.get("relevance") or 0), record.get("tier", "excluded"),
                record.get("relevance_reason"), json.dumps(hits, ensure_ascii=False),
                json.dumps(sources, ensure_ascii=False), json.dumps(source_ids, ensure_ascii=False),
                now, now, now,
            ),
        )
        work_id = int(cur.lastrowid)
        self._sync_authors(work_id, authors)
        return work_id, True

    def _sync_authors(self, work_id: int, authors: list[dict]) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM work_authors WHERE work_id = ?", (work_id,))
        for pos, author in enumerate(authors):
            name = (author.get("name") or "").strip()
            if not name:
                continue
            # "Wei-Wei Zhao", "Weiwei Zhao" and "Wei‐Wei Zhao" are one person.
            norm = re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", normalize(name))
            row = conn.execute("SELECT id FROM authors WHERE norm_name = ?", (norm,)).fetchone()
            if row:
                author_id = row["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO authors(name, norm_name, orcid) VALUES (?,?,?)",
                    (name, norm, author.get("orcid")),
                )
                author_id = int(cur.lastrowid)
            conn.execute(
                "INSERT OR IGNORE INTO work_authors(work_id, author_id, position) VALUES (?,?,?)",
                (work_id, author_id, pos),
            )

    # ----------------------------------------------------------------- query
    def query_works(
        self,
        *,
        q: str = "",
        year_from: int | None = None,
        year_to: int | None = None,
        venue: str = "",
        topic: str = "",
        tier: str = "",
        source: str = "",
        author: str = "",
        favorite: bool = False,
        days: int | None = None,
        sort: str = "newest",
        page: int = 1,
        per_page: int = 20,
        min_relevance: float | None = None,
    ) -> dict[str, Any]:
        conn = self._conn()
        where: list[str] = []
        params: dict[str, Any] = {}

        if min_relevance is not None:
            where.append("w.relevance >= :min_rel")
            params["min_rel"] = float(min_relevance)

        if q.strip():
            parts = [p for p in re.split(r"\s+", q.strip()) if p]
            cjk_tokens = [p for p in parts if re.search(r"[\u3400-\u9fff]", p)]
            latin_tokens = [p for p in parts if not re.search(r"[\u3400-\u9fff]", p)]

            # FTS5's default tokenizer does not segment Chinese, so Chinese
            # tokens use LIKE (title / abstract / topic tags).
            for i, token in enumerate(cjk_tokens):
                key = f"like{i}"
                where.append(
                    f"(w.title LIKE :{key} OR w.abstract LIKE :{key} OR w.topics_json LIKE :{key})"
                )
                params[key] = f"%{token}%"

            if latin_tokens:
                if self.fts_enabled:
                    safe = [re.sub(r'["*()]', " ", t).strip() for t in latin_tokens]
                    safe = [t for t in safe if t]
                    if safe:
                        where.append("w.id IN (SELECT rowid FROM works_fts WHERE works_fts MATCH :fts)")
                        params["fts"] = " AND ".join(f'"{t}"*' for t in safe)
                    else:
                        where.append("(w.title LIKE :like OR w.abstract LIKE :like)")
                        params["like"] = f"%{' '.join(latin_tokens)}%"
                else:
                    where.append("(w.title LIKE :like OR w.abstract LIKE :like)")
                    params["like"] = f"%{' '.join(latin_tokens)}%"

        if year_from:
            where.append("w.published_year >= :year_from")
            params["year_from"] = year_from
        if year_to:
            where.append("w.published_year <= :year_to")
            params["year_to"] = year_to
        if venue:
            where.append("lower(w.venue) = lower(:venue)")
            params["venue"] = venue
        if tier:
            where.append("w.tier = :tier")
            params["tier"] = tier
        if source:
            where.append("w.sources_json LIKE :source")
            params["source"] = f'%"{source}"%'
        if favorite:
            where.append("w.favorite = 1")
        if days:
            cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
            where.append("w.published_date >= :cutoff")
            params["cutoff"] = cutoff
        if topic:
            where.append("w.topics_json LIKE :topic")
            params["topic"] = f'%"{topic}"%'
        if author:
            where.append(
                """w.id IN (SELECT wa.work_id FROM work_authors wa
                            JOIN authors a ON a.id = wa.author_id
                            WHERE a.norm_name LIKE :author)"""
            )
            params["author"] = f"%{normalize(author)}%"

        clause = (" WHERE " + " AND ".join(where)) if where else ""
        order = {
            "newest": "w.published_date DESC, w.relevance DESC",
            "oldest": "w.published_date ASC, w.relevance DESC",
            "relevance": "w.relevance DESC, w.published_date DESC",
            "citations": "w.cited_by DESC, w.published_date DESC",
        }.get(sort, "w.published_date DESC")

        total = conn.execute(f"SELECT count(*) FROM works w{clause}", params).fetchone()[0]
        page = max(1, page)
        offset = (page - 1) * per_page
        rows = conn.execute(
            f"SELECT w.* FROM works w{clause} ORDER BY {order} LIMIT :limit OFFSET :offset",
            {**params, "limit": per_page, "offset": offset},
        ).fetchall()

        return {
            "items": [row_to_work(r) for r in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
        }

    def get_work(self, work_id: int) -> dict | None:
        row = self._conn().execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
        return row_to_work(row) if row else None

    def get_work_by_doi(self, doi: str) -> dict | None:
        row = self._conn().execute("SELECT * FROM works WHERE doi = ?", (doi.lower(),)).fetchone()
        return row_to_work(row) if row else None

    def recent(self, limit: int = 12, days: int | None = 30) -> list[dict]:
        conn = self._conn()
        params: list[Any] = []
        clause = "WHERE relevance >= 30"
        if days:
            cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
            clause += " AND published_date >= ?"
            params.append(cutoff)
        rows = conn.execute(
            f"SELECT * FROM works {clause} ORDER BY published_date DESC, relevance DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [row_to_work(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        conn = self._conn()
        total = conn.execute("SELECT count(*) FROM works WHERE relevance >= 30").fetchone()[0]
        core = conn.execute("SELECT count(*) FROM works WHERE tier='core'").fetchone()[0]
        week_cut = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
        month_cut = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
        this_week = conn.execute(
            "SELECT count(*) FROM works WHERE relevance >= 30 AND published_date >= ?", (week_cut,)
        ).fetchone()[0]
        this_month = conn.execute(
            "SELECT count(*) FROM works WHERE relevance >= 30 AND published_date >= ?", (month_cut,)
        ).fetchone()[0]
        years = [
            dict(r)
            for r in conn.execute(
                """SELECT published_year AS year, count(*) AS count FROM works
                   WHERE relevance >= 30 AND published_year IS NOT NULL
                   GROUP BY published_year ORDER BY published_year"""
            ).fetchall()
        ]
        months = [
            dict(r)
            for r in conn.execute(
                """SELECT substr(published_date,1,7) AS month, count(*) AS count FROM works
                   WHERE relevance >= 30 AND published_date >= ?
                   GROUP BY month ORDER BY month""",
                ((datetime.now(timezone.utc).date() - timedelta(days=730)).isoformat(),),
            ).fetchall()
        ]
        venues = merge_venues(
            conn.execute(
                """SELECT venue, count(*) AS count FROM works
                   WHERE relevance >= 30 AND venue IS NOT NULL AND venue != ''
                   GROUP BY venue"""
            ).fetchall()
        )[:12]
        topics: dict[str, int] = {}
        for row in conn.execute("SELECT topics_json FROM works WHERE relevance >= 30").fetchall():
            for t in json.loads(row["topics_json"] or "[]"):
                topics[t] = topics.get(t, 0) + 1
        sources: dict[str, int] = {}
        for row in conn.execute("SELECT sources_json FROM works WHERE relevance >= 30").fetchall():
            for s in json.loads(row["sources_json"] or "[]"):
                sources[s] = sources.get(s, 0) + 1
        top_authors = [
            dict(r)
            for r in conn.execute(
                """SELECT a.name, a.id, count(*) AS count FROM authors a
                   JOIN work_authors wa ON wa.author_id = a.id
                   JOIN works w ON w.id = wa.work_id
                   WHERE w.relevance >= 30
                   GROUP BY a.id ORDER BY count DESC, a.name LIMIT 15"""
            ).fetchall()
        ]
        latest_sync = conn.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "total": total,
            "core": core,
            "week": this_week,
            "month": this_month,
            "years": years,
            "months": months,
            "venues": venues,
            "topics": sorted(topics.items(), key=lambda kv: -kv[1]),
            "sources": sources,
            "authors": top_authors,
            "latest_sync": dict(latest_sync) if latest_sync else None,
            "favorites": conn.execute("SELECT count(*) FROM works WHERE favorite=1").fetchone()[0],
        }

    def facets(self) -> dict[str, Any]:
        conn = self._conn()
        venues = merge_venues(
            conn.execute(
                """SELECT venue, count(*) AS count FROM works
                   WHERE relevance >= 30 AND venue IS NOT NULL AND venue != ''
                   GROUP BY venue"""
            ).fetchall()
        )[:40]
        topics: dict[str, int] = {}
        for row in conn.execute("SELECT topics_json FROM works WHERE relevance >= 30").fetchall():
            for t in json.loads(row["topics_json"] or "[]"):
                topics[t] = topics.get(t, 0) + 1
        years = [
            dict(r)
            for r in conn.execute(
                """SELECT published_year AS year, count(*) AS count FROM works
                   WHERE relevance >= 30 AND published_year IS NOT NULL
                   GROUP BY published_year ORDER BY published_year DESC"""
            ).fetchall()
        ]
        return {
            "venues": venues,
            "topics": sorted(topics.items(), key=lambda kv: -kv[1]),
            "years": years,
        }

    def timeline(self, tier: str = "") -> list[dict[str, Any]]:
        conn = self._conn()
        clause = "WHERE relevance >= 30"
        params: list[Any] = []
        if tier:
            clause += " AND tier = ?"
            params.append(tier)
        rows = conn.execute(
            f"SELECT * FROM works {clause} ORDER BY published_date DESC", params
        ).fetchall()
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            work = row_to_work(row)
            key = (work.get("published_date") or "")[:7] or "unknown"
            bucket = groups.setdefault(key, {"month": key, "items": []})
            bucket["items"].append(work)
        return [groups[k] for k in sorted(groups, reverse=True)]

    # -------------------------------------------------------------- mutations
    def rebuild_authors(self) -> dict[str, int]:
        """Re-derive the author index with the current name normalisation."""
        conn = self._conn()
        rows = conn.execute("SELECT id, authors_json FROM works").fetchall()
        conn.execute("DELETE FROM work_authors")
        conn.execute("DELETE FROM authors")
        for row in rows:
            try:
                authors = json.loads(row["authors_json"] or "[]")
            except ValueError:
                authors = []
            self._sync_authors(row["id"], authors)
        conn.commit()
        return {
            "works": len(rows),
            "authors": conn.execute("SELECT count(*) FROM authors").fetchone()[0],
        }

    def rescore_all(self, scorer, *, prune_below: float | None = None) -> dict[str, int]:
        """Recompute relevance for every stored work with the current scorer."""
        conn = self._conn()
        rows = conn.execute("SELECT * FROM works").fetchall()
        changed = 0
        pruned = 0
        for row in rows:
            work = row_to_work(row)
            score = scorer.score(
                work.get("title"),
                work.get("abstract"),
                keywords=work.get("keywords"),
                venue=work.get("venue"),
            )
            if prune_below is not None and score.score < prune_below:
                conn.execute("DELETE FROM work_authors WHERE work_id = ?", (work["id"],))
                conn.execute("DELETE FROM works WHERE id = ?", (work["id"],))
                pruned += 1
                continue
            if abs(float(work.get("relevance") or 0) - score.score) >= 0.05 or work.get("tier") != score.tier:
                changed += 1
            conn.execute(
                "UPDATE works SET relevance=?, tier=?, relevance_reason=?, topics_json=?, hits_json=? WHERE id=?",
                (
                    score.score,
                    score.tier,
                    score.reason,
                    json.dumps(score.topics, ensure_ascii=False),
                    json.dumps(
                        [
                            {"label": h.label, "weight": h.weight, "title": h.title, "abstract": h.abstract}
                            for h in score.hits
                        ],
                        ensure_ascii=False,
                    ),
                    work["id"],
                ),
            )
        conn.commit()
        return {"scanned": len(rows), "changed": changed, "pruned": pruned}

    def set_flag(self, work_id: int, field: str, value: Any) -> bool:
        if field not in {"favorite", "read_status", "note"}:
            return False
        with self.connect() as conn:
            conn.execute(f"UPDATE works SET {field} = ? WHERE id = ?", (value, work_id))
        return True

    def get_settings(self) -> dict[str, Any]:
        rows = self._conn().execute("SELECT key, value FROM settings").fetchall()
        out = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except (TypeError, ValueError):
                out[r["key"]] = r["value"]
        return out

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    # ------------------------------------------------------------- sync runs
    def start_sync(self, trigger: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO sync_runs(started_at, status, trigger) VALUES (?, 'running', ?)",
                (utcnow(), trigger),
            )
            return int(cur.lastrowid)

    def update_sync(self, run_id: int, stats: dict, status: str = "running", message: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sync_runs SET stats_json = ?, status = ?, message = ? WHERE id = ?",
                (json.dumps(stats, ensure_ascii=False), status, message, run_id),
            )

    def finish_sync(self, run_id: int, stats: dict, status: str = "success", message: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sync_runs SET finished_at = ?, stats_json = ?, status = ?, message = ? WHERE id = ?",
                (utcnow(), json.dumps(stats, ensure_ascii=False), status, message, run_id),
            )

    def last_success_sync(self) -> dict | None:
        row = self._conn().execute(
            "SELECT * FROM sync_runs WHERE status = 'success' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def list_syncs(self, limit: int = 20) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def all_works_for_export(self, min_relevance: float = 30) -> Iterable[dict]:
        rows = self._conn().execute(
            "SELECT * FROM works WHERE relevance >= ? ORDER BY published_date DESC",
            (min_relevance,),
        ).fetchall()
        for row in rows:
            yield row_to_work(row)


def _venue_key(name: str) -> str:
    key = name.strip().lower().replace("&", " and ")
    key = re.sub(r"\([^)]*\)", " ", key)
    key = re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def _venue_display(name: str) -> str:
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name.strip())
    words = name.split()
    caps = [w for w in words if len(w) > 1 and w.isupper()]
    if len(caps) >= 2 and not any(w.islower() for w in words):
        return name  # e.g. "ACS APPLIED MATERIALS" -> keep the acronym look
    # "Analytical chemistry" is a casing artefact, not a real title: title-case it.
    if len(words) > 1 and not any(w.isupper() for w in words[1:]):
        name = name[:1].upper() + name[1:]
    small = {"and", "of", "the", "in", "on", "for", "a", "an", "to", "with"}
    out = []
    for i, word in enumerate(words):
        if word.isupper() and len(word) > 1:
            out.append(word)
        elif i and word.lower() in small:
            out.append(word.lower())
        else:
            out.append(word[:1].upper() + word[1:].lower())
    return " ".join(out)


def merge_venues(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    """Case/punctuation-insensitive venue grouping with a cleaned display name."""
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = (row["venue"] or "").strip()
        if not name:
            continue
        key = _venue_key(name)
        entry = groups.setdefault(key, {"variants": {}, "count": 0})
        entry["variants"][name] = entry["variants"].get(name, 0) + row["count"]
        entry["count"] += row["count"]
    out = []
    for key, entry in groups.items():
        display = _venue_display(max(entry["variants"].items(), key=lambda kv: kv[1])[0])
        out.append({"venue": display, "count": entry["count"]})
    return sorted(out, key=lambda v: (-v["count"], v["venue"].lower()))


def row_to_work(row: sqlite3.Row | dict) -> dict[str, Any]:
    d = dict(row)
    for src, dst, default in (
        ("authors_json", "authors", []),
        ("topics_json", "topics", []),
        ("keywords_json", "keywords", []),
        ("hits_json", "hits", []),
        ("sources_json", "sources", []),
        ("source_ids_json", "source_ids", {}),
    ):
        try:
            d[dst] = json.loads(d.get(src) or json.dumps(default))
        except (TypeError, ValueError):
            d[dst] = default
        d.pop(src, None)
    d["favorite"] = bool(d.get("favorite"))
    d["is_open_access"] = bool(d.get("is_open_access"))
    return d
