"""Export the tracker as a fully static site (GitHub Pages / any static host).

The server version renders pages on demand; this module renders every page once,
rewrites internal links for static hosting, and adds a small client-side layer
(search, filters, sorting, favourites and notes kept in localStorage).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import templates as T
from . import views
from .db import Database, merge_venues
from .sync import load_config

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------- helpers
def _stub_engine() -> Any:
    """views.home() only needs engine.state.as_dict()."""
    return SimpleNamespace(state=SimpleNamespace(as_dict=lambda: {}), running=False)


def _map_path(path: str, base: str) -> str:
    if path.startswith("/static/"):
        return f"{base}{path}"
    if path.startswith("/paper/"):
        pid = path[len("/paper/"):].split("?")[0].rstrip("/")
        return f"{base}/paper/{pid}.html"
    if path.startswith("/papers"):
        query = "?" + path.split("?", 1)[1] if "?" in path else ""
        return f"{base}/papers.html{query}"
    if path.startswith("/stats"):
        return f"{base}/stats.html"
    if path.startswith("/timeline"):
        return f"{base}/timeline.html"
    if path.startswith("/export.csv"):
        return f"{base}/data/works.csv"
    if path.startswith("/export.bib"):
        return f"{base}/data/works.bib"
    if path.startswith("/export.json"):
        return f"{base}/data/works.json"
    if path == "/":
        return f"{base}/index.html"
    return path


def _rewrite(html: str, base: str, stamp: str) -> str:
    # 设置页在静态站不存在
    html = re.sub(r'<a class="nav-item[^"]*" href="/settings">.*?</a>', "", html, flags=re.S)
    # 同步按钮改为静态标记
    html = re.sub(r'<button class="btn btn-ghost" id="sync-btn".*?</button>', "", html, flags=re.S)
    html = re.sub(
        r'<button class="btn btn-primary" id="sync-btn-hero".*?</button>',
        f'<span class="btn btn-ghost static-stamp">{T.icon("clock")}数据更新于 {stamp}</span>',
        html,
        flags=re.S,
    )
    html = html.replace("/static/app.js", "/static/static.js")
    html = html.replace(
        "仅作科研信息聚合，请以原文为准",
        f"静态站 · 更新于 {stamp} · 仅作科研信息聚合，请以原文为准",
    )
    return re.sub(
        r'(href|src|action)="(/[^"]*)"',
        lambda m: f'{m.group(1)}="{_map_path(m.group(2), base)}"',
        html,
    )


def _card(w: dict) -> str:
    """paper_card() plus data-attributes for the client-side filters."""
    card = T.paper_card(w)
    topics = "|".join(w.get("topics") or [])
    sources = "|".join(w.get("sources") or [])
    authors = "|".join(a.get("name", "") for a in (w.get("authors") or []))
    venue = T._clean_venue(w.get("venue"))
    attrs = (
        f'data-id="{w["id"]}" data-tier="{T.esc(w.get("tier", ""))}" '
        f'data-year="{T.esc(w.get("published_year") or "")}" '
        f'data-topics="{T.esc(topics)}" data-sources="{T.esc(sources)}" '
        f'data-authors="{T.esc(authors)}" '
        f'data-venue="{T.esc(venue)}" data-relevance="{float(w.get("relevance") or 0):.1f}" '
        f'data-date="{T.esc(w.get("published_date") or "")}" '
        f'data-cites="{int(w.get("cited_by") or 0)}"'
    )
    return card.replace(f'data-id="{w["id"]}"', attrs, 1)


# ----------------------------------------------------------------- papers page
def _papers_page(db: Database, cfg: dict, base: str, stamp: str) -> str:
    site = cfg["site"]
    stats = db.stats()
    items = db.query_works(per_page=5000, sort="newest", min_relevance=float(cfg["relevance"].get("include_threshold", 30)))["items"]
    facets = db.facets()

    cards = "".join(_card(w) for w in items)

    tier_rows = [
        ("core", "核心 OPECT"),
        ("related", "强相关"),
        ("peripheral", "外围相关"),
    ]
    tier_counts = {
        key: sum(1 for w in items if w.get("tier") == key) for key, _ in tier_rows
    }

    def group(title: str, key: str, rows: list[tuple[str, int]], limit: int = 40, open_: bool = False) -> str:
        body = "".join(
            f'<button class="facet-item" type="button" data-facet="{key}" data-value="{T.esc(str(value))}">'
            f"<span>{T.esc(str(value))}</span><b>{T.fmt_num(count)}</b></button>"
            for value, count in rows[:limit]
            if count
        )
        return (
            f'<details class="facet"{" open" if open_ else ""}><summary>{T.esc(title)}</summary>'
            f'<div class="facet-list">{body}</div></details>'
        )

    venues = merge_venues(
        db._conn().execute(
            "SELECT venue, count(*) AS count FROM works WHERE relevance>=30 AND venue<>'' GROUP BY lower(venue) ORDER BY count DESC"
        ).fetchall()
    )
    source_counts = db.stats().get("sources") or {}
    source_rows = [
        (label, source_counts.get(key, 0))
        for key, label in [("openalex", "OpenAlex"), ("crossref", "Crossref"), ("pubmed", "PubMed")]
    ]

    body = f"""
<div class="page-head">
  <div>
    <span class="eyebrow">LIBRARY</span>
    <h1>全部文献</h1>
    <p>共 <strong id="total-count">{T.fmt_num(len(items))}</strong> 篇收录文献，支持本地检索与筛选。</p>
  </div>
  <div class="page-actions">
    <div class="segmented" id="sort-seg">
      <button class="sort-opt active" type="button" data-sort="newest">最新发表</button>
      <button class="sort-opt" type="button" data-sort="relevance">相关性</button>
      <button class="sort-opt" type="button" data-sort="citations">被引最多</button>
      <button class="sort-opt" type="button" data-sort="oldest">最早发表</button>
    </div>
    <div class="export-menu">
      <button class="btn btn-ghost" type="button" data-export-toggle>{T.icon('download')}导出</button>
      <div class="export-pop" hidden>
        <a href="{base}/data/works.csv">CSV 表格</a>
        <a href="{base}/data/works.bib">BibTeX</a>
        <a href="{base}/data/works.json">JSON</a>
      </div>
    </div>
  </div>
</div>
<div class="library">
  <aside class="facets">
    <div class="facet-title">{T.icon('filter')}筛选</div>
    {group("相关性层级", "tier", [(label, tier_counts.get(key, 0)) for key, label in tier_rows], open_=True)}
    {group("发表年份", "year", [(str(y["year"]), y["count"]) for y in facets["years"]])}
    {group("研究主题", "topic", [(t, n) for t, n in facets["topics"]], 40)}
    {group("期刊 / 会议", "venue", [(v["venue"], v["count"]) for v in venues], 40)}
    {group("数据来源", "source", source_rows)}
    <button class="facet-item" type="button" data-facet="favorite" data-value="1">
      <span class="fav-facet">{T.icon('star')}仅看收藏</span><b id="fav-count">0</b>
    </button>
  </aside>
  <section class="results">
    <div id="active-chips" class="chips"></div>
    <div class="result-meta"><span id="result-count"></span><span id="result-note" class="muted"></span></div>
    <div class="paper-list" id="paper-list">{cards}</div>
    <div id="no-result" hidden>{T.empty_state("没有匹配的文献", "尝试放宽筛选条件，或更换关键词。")}</div>
  </section>
</div>
"""
    return T.layout(title="全部文献", body=body, active="papers", site=site, stats=stats, wide=True)


# --------------------------------------------------------------------- exports
def _export_files(db: Database, out: Path, threshold: float) -> None:
    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    items = db.query_works(per_page=5000, sort="newest", min_relevance=threshold)["items"]

    with (data_dir / "works.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["id", "title", "authors", "venue", "year", "date", "doi", "relevance", "tier", "topics", "cited_by"]
        )
        for w in items:
            writer.writerow(
                [
                    w["id"],
                    w.get("title", ""),
                    "; ".join(a.get("name", "") for a in (w.get("authors") or [])),
                    T._clean_venue(w.get("venue")),
                    w.get("published_year") or "",
                    w.get("published_date") or "",
                    w.get("doi") or "",
                    round(float(w.get("relevance") or 0), 1),
                    w.get("tier") or "",
                    "; ".join(w.get("topics") or []),
                    w.get("cited_by") or 0,
                ]
            )

    bib = []
    for w in items:
        key = (w.get("doi") or f"opect{w['id']}").replace("/", "_").replace(":", "_")
        authors = " and ".join(a.get("name", "") for a in (w.get("authors") or []))
        fields = [
            f"  title = {{{w.get('title', '')}}}",
            f"  author = {{{authors}}}" if authors else "",
            f"  journal = {{{T._clean_venue(w.get('venue'))}}}" if w.get("venue") else "",
            f"  year = {{{w.get('published_year')}}}" if w.get("published_year") else "",
            f"  doi = {{{w.get('doi')}}}" if w.get("doi") else "",
            f"  url = {{https://doi.org/{w.get('doi')}}}" if w.get("doi") else "",
        ]
        bib.append(f"@article{{{key},\n" + ",\n".join(x for x in fields if x) + "\n}")
    (data_dir / "works.bib").write_text("\n\n".join(bib), encoding="utf-8")

    payload = [
        {
            "id": w["id"],
            "title": w.get("title"),
            "abstract": w.get("abstract"),
            "authors": [a.get("name") for a in (w.get("authors") or [])],
            "venue": T._clean_venue(w.get("venue")),
            "year": w.get("published_year"),
            "date": w.get("published_date"),
            "doi": w.get("doi"),
            "url": f"https://doi.org/{w['doi']}" if w.get("doi") else None,
            "relevance": round(float(w.get("relevance") or 0), 1),
            "tier": w.get("tier"),
            "topics": w.get("topics"),
            "cited_by": w.get("cited_by"),
            "open_access": bool(w.get("is_open_access")),
        }
        for w in items
    ]
    (data_dir / "works.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ------------------------------------------------------------------------ build
def build(db_path: str | Path, out_dir: str | Path, base: str = "") -> dict[str, int]:
    cfg = load_config()
    db = Database(str(db_path))
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "paper").mkdir(parents=True, exist_ok=True)
    (out / "static").mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(views.site_timezone()).strftime("%Y-%m-%d %H:%M")
    engine = _stub_engine()

    for name in ("style.css", "static.js", "favicon.svg"):
        src = ROOT / "static" / name
        if src.exists():
            shutil.copy2(src, out / "static" / name)

    pages: list[tuple[str, str]] = []
    pages.append(("index.html", views.home(db, engine)))
    pages.append(("papers.html", _papers_page(db, cfg, base, stamp)))
    pages.append(("stats.html", views.stats_page(db)))
    pages.append(("timeline.html", views.timeline_page(db, {})))

    written = 0
    for name, html in pages:
        (out / name).write_text(_rewrite(html, base, stamp), encoding="utf-8")
        written += 1

    threshold = float(cfg["relevance"].get("include_threshold", 30))
    for w in db.query_works(per_page=5000, sort="newest", min_relevance=threshold)["items"]:
        html = views.paper_page(db, w["id"])
        if html:
            (out / "paper" / f"{w['id']}.html").write_text(_rewrite(html, base, stamp), encoding="utf-8")
            written += 1

    (out / ".nojekyll").write_text("", encoding="utf-8")
    _export_files(db, out, threshold)
    return {"pages": written, "works": db.stats()["total"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build static OPECT site")
    parser.add_argument("--db", default=str(ROOT / "data" / "opect.db"))
    parser.add_argument("--out", default=str(ROOT / "docs"))
    parser.add_argument("--base", default="", help="site base path, e.g. /opect for project pages")
    args = parser.parse_args(argv)
    base = args.base.rstrip("/")
    result = build(args.db, args.out, base)
    print(f"静态站已生成：{args.out}（{result['pages']} 个页面，{result['works']} 篇文献）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

