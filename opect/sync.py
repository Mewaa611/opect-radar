"""Sync engine: fetch -> normalize -> dedupe -> score -> store."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import sources
from .db import Database, utcnow
from .scoring import RelevanceScorer

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else CONFIG_PATH
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class SyncState:
    running: bool = False
    run_id: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    stage: str = "idle"
    current_source: str = ""
    current_query: str = ""
    progress: float = 0.0
    stats: dict[str, Any] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stage": self.stage,
            "current_source": self.current_source,
            "current_query": self.current_query,
            "progress": round(self.progress, 1),
            "stats": self.stats,
            "log": self.log[-60:],
            "error": self.error,
        }


class SyncEngine:
    """Serialises sync runs and exposes live progress."""

    def __init__(self, db: Database, config: dict[str, Any]):
        self.db = db
        self.config = config
        self.scorer = RelevanceScorer(config)
        self.state = SyncState()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ public
    def start(self, trigger: str = "manual") -> dict[str, Any]:
        if self.state.running:
            return {"ok": False, "message": "同步已在进行中", "state": self.state.as_dict()}
        self._thread = threading.Thread(target=self._run, args=(trigger,), daemon=True)
        self._thread.start()
        return {"ok": True, "message": "同步已启动"}

    def stop(self) -> None:
        self._stop.set()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    # -------------------------------------------------------------------- run
    def _run(self, trigger: str) -> None:
        with self._lock:
            self.state = SyncState(
                running=True,
                started_at=utcnow(),
                stage="starting",
                log=["开始同步"],
            )
        run_id = self.db.start_sync(trigger)
        self.state.run_id = run_id
        stats: dict[str, Any] = {
            "fetched": 0,
            "unique": 0,
            "stored": 0,
            "new": 0,
            "updated": 0,
            "relevant": 0,
            "dropped": 0,
            "errors": [],
            "by_source": {},
            "duration_s": 0,
        }
        started = time.time()
        try:
            cfg = self.config.get("sync", {})
            date_from, date_to = self._date_window(cfg)
            self.state.stage = "fetching"
            seen: set[str] = set()
            plan = self._build_plan(cfg)
            total_queries = sum(len(v) for v in plan.values()) or 1
            done = 0

            for source, queries in plan.items():
                if self._stop.is_set():
                    break
                src_stats = stats["by_source"].setdefault(
                    source, {"fetched": 0, "stored": 0, "relevant": 0, "errors": 0}
                )
                self.state.current_source = source
                for query in queries:
                    if self._stop.is_set():
                        break
                    self.state.current_query = query
                    self.state.stage = f"{source}: {query[:60]}"
                    try:
                        records = list(
                            self._fetch(source, query, date_from, date_to, cfg)
                        )
                    except sources.FetchError as exc:
                        src_stats["errors"] += 1
                        stats["errors"].append(f"{source}: {exc}")
                        self.state.log.append(f"抓取失败 {source} / {query[:40]}: {exc}")
                        done += 1
                        self.state.progress = 100 * done / total_queries
                        continue

                    src_stats["fetched"] += len(records)
                    stats["fetched"] += len(records)
                    for rec in records:
                        key = rec.get("doi") or rec["normalized_title"]
                        if key in seen:
                            continue
                        seen.add(key)
                        stats["unique"] += 1
                        self._handle_record(rec, stats, src_stats)
                    done += 1
                    self.state.progress = 100 * done / total_queries
                    self.state.stats = dict(stats)
                    self.state.log.append(
                        f"{source} · {query[:48]} → {len(records)} 条，入库 {src_stats['stored']}，相关 {src_stats['relevant']}"
                    )

            self.state.stage = "finalizing"
            self.state.progress = 100
            stats["duration_s"] = round(time.time() - started, 1)
            self.state.stats = dict(stats)
            self.state.running = False
            self.state.finished_at = utcnow()
            self.state.stage = "done"
            summary = (
                f"抓取 {stats['fetched']} 条，去重后 {stats['unique']} 条，"
                f"新增 {stats['new']} 条，相关 {stats['relevant']} 条"
            )
            self.state.log.append("完成：" + summary)
            self.db.finish_sync(run_id, stats, "success", summary)
        except Exception as exc:  # noqa: BLE001
            stats["duration_s"] = round(time.time() - started, 1)
            self.state.error = str(exc)
            self.state.running = False
            self.state.finished_at = utcnow()
            self.state.stage = "error"
            self.state.log.append(f"同步失败：{exc}")
            self.db.finish_sync(run_id, stats, "error", str(exc))
        finally:
            self._stop.clear()

    # ----------------------------------------------------------------- helpers
    def _date_window(self, cfg: dict) -> tuple[str, str]:
        today = datetime.now(timezone.utc).date()
        date_to = today.isoformat()
        last = self.db.last_success_sync()
        has_data = self.db._conn().execute("SELECT count(*) FROM works").fetchone()[0] > 0
        if last and last.get("finished_at") and has_data:
            try:
                last_dt = datetime.fromisoformat(last["finished_at"])
                overlap = int(cfg.get("overlap_days", 45))
                start = (last_dt - timedelta(days=overlap)).date()
            except ValueError:
                start = today - timedelta(days=int(cfg.get("initial_lookback_days", 3650)))
        else:
            start = today - timedelta(days=int(cfg.get("initial_lookback_days", 3650)))
        min_year = int(cfg.get("min_year", 2015))
        start = max(start, date(min_year, 1, 1))
        return start.isoformat(), date_to

    def _build_plan(self, cfg: dict) -> dict[str, list[str]]:
        enabled = cfg.get("sources", {})
        queries = self.config.get("queries", {})
        return {
            src: list(queries.get(src, []))
            for src in ("openalex", "crossref", "pubmed", "arxiv")
            if enabled.get(src) and queries.get(src)
        }

    def _fetch(
        self, source: str, query: str, date_from: str, date_to: str, cfg: dict
    ) -> list[dict]:
        mailto = (cfg.get("contact_email") or "").strip() or None
        if source == "openalex":
            raw = sources.openalex_search(
                query,
                date_from=date_from,
                date_to=date_to,
                per_page=int(cfg.get("per_page", 100)),
                max_pages=int(cfg.get("max_pages_per_query", 5)),
                mailto=mailto,
            )
            return [r for r in (sources.parse_openalex(x) for x in raw) if r]
        if source == "crossref":
            raw = sources.crossref_search(
                query,
                date_from=date_from,
                date_to=date_to,
                rows=int(cfg.get("per_page", 100)),
                mailto=mailto,
            )
            return [r for r in (sources.parse_crossref(x) for x in raw) if r]
        if source == "pubmed":
            raw = sources.pubmed_search(
                query,
                date_from=date_from,
                date_to=date_to,
                retmax=200,
                mailto=mailto,
            )
            return [r for r in raw if r]
        if source == "arxiv":
            return [r for r in sources.arxiv_search(query, max_results=100) if r]
        return []

    def _handle_record(self, rec: dict, stats: dict, src_stats: dict) -> None:
        title = rec.get("title")
        abstract = rec.get("abstract")
        score = self.scorer.score(
            title,
            abstract,
            keywords=rec.get("keywords"),
            venue=rec.get("venue"),
        )
        store_threshold = float(self.config.get("relevance", {}).get("store_threshold", 18))
        if score.score < store_threshold:
            stats["dropped"] += 1
            return
        rec["relevance"] = score.score
        rec["tier"] = score.tier
        rec["relevance_reason"] = score.reason
        rec["topics"] = score.topics
        rec["hits"] = [
            {"label": h.label, "weight": h.weight, "title": h.title, "abstract": h.abstract}
            for h in score.hits
        ]
        try:
            _work_id, created = self.db.upsert_work(rec)
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"入库失败: {rec.get('title', '')[:50]} ({exc})")
            return
        stats["stored"] += 1
        src_stats["stored"] += 1
        stats["new" if created else "updated"] += 1
        if score.score >= float(self.config.get("relevance", {}).get("include_threshold", 30)):
            stats["relevant"] += 1
            src_stats["relevant"] += 1

    # ------------------------------------------------------------------ auto
    def auto_loop(self, on_tick: Callable[[], None] | None = None) -> None:
        """Background scheduler: sync on start (if stale) and every N minutes."""
        cfg = self.config.get("sync", {})
        interval = max(15, int(cfg.get("interval_minutes", 360))) * 60
        if cfg.get("on_start", True):
            last = self.db.last_success_sync()
            stale = True
            if last and last.get("finished_at"):
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(last["finished_at"])).total_seconds()
                    stale = age > interval
                except ValueError:
                    stale = True
            if stale:
                time.sleep(2)
                self.start("auto-start")
        while True:
            for _ in range(interval // 10):
                if self._stop.wait(10):
                    return
            if not self.state.running:
                self.start("auto")
            if on_tick:
                on_tick()
