"""Page rendering for the OPECT literature radar."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode

from . import templates as T
from .db import Database
from .scoring import RelevanceScorer
from .sync import SyncEngine, load_config

PAGE_SIZE = 20


def _today() -> date:
    return datetime.now(timezone.utc).date()


def daily_counts(db: Database, days: int = 30) -> list[tuple[str, int]]:
    start = _today() - timedelta(days=days - 1)
    rows = db._conn().execute(
        """SELECT published_date, count(*) AS c FROM works
           WHERE relevance >= 30 AND published_date >= ? GROUP BY published_date""",
        (start.isoformat(),),
    ).fetchall()
    counts = {r["published_date"]: r["c"] for r in rows}
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        out.append((d.isoformat(), counts.get(d.isoformat(), 0)))
    return out


def monthly_counts(db: Database, months: int = 24) -> list[tuple[str, int]]:
    today = _today()
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    keys.reverse()
    rows = db._conn().execute(
        """SELECT substr(published_date,1,7) AS k, count(*) AS c FROM works
           WHERE relevance >= 30 AND published_date >= ? GROUP BY k""",
        (keys[0] + "-01",),
    ).fetchall()
    counts = {r["k"]: r["c"] for r in rows}
    return [(k, counts.get(k, 0)) for k in keys]


# --------------------------------------------------------------------- home
def home(db: Database, engine: SyncEngine) -> str:
    cfg = load_config()
    site = cfg["site"]
    stats = db.stats()
    recent = db.recent(limit=10, days=None)
    daily = daily_counts(db, 21)
    spark = T.sparkline([c for _, c in daily], width=240, height=44)
    sync = engine.state.as_dict()
    latest = stats.get("latest_sync") or {}
    sync_txt = sync_time_label(latest.get("finished_at")) if latest.get("finished_at") else "尚未同步"
    stats_row = (
        T.stat_card("追踪文献", T.fmt_num(stats["total"]), "相关性 ≥ 30 分", "accent")
        + T.stat_card("核心 OPECT", T.fmt_num(stats["core"]), "明确命中核心概念", "")
        + T.stat_card("近 7 天", T.fmt_num(stats["week"]), "新上线文献", "")
        + T.stat_card("近 30 天", T.fmt_num(stats["month"]), f"上次同步 {sync_txt}", "")
    )
    topics_html = "".join(
        f'<a class="topic-pill" href="/papers?topic={quote(t)}">{T.esc(t)}<b>{n}</b></a>'
        for t, n in stats["topics"][:10]
    )
    venues_html = "".join(
        f'<a class="mini-row" href="/papers?venue={T.esc(v["venue"])}">'
        f'<span>{T.esc(v["venue"])}</span><b>{v["count"]}</b></a>'
        for v in stats["venues"][:6]
    )
    authors_html = "".join(
        f'<a class="mini-row" href="/papers?author={T.esc(a["name"])}">'
        f'<span>{T.esc(a["name"])}</span><b>{a["count"]}</b></a>'
        for a in stats["authors"][:6]
    )
    feed = "".join(T.paper_card(w) for w in recent) or T.empty_state(
        "还没有文献", "点击右上角「同步」抓取最新 OPECT 文献，首次全量同步约需 1–3 分钟。"
    )
    body = f"""
<section class="hero">
  <div class="hero-copy">
    <span class="eyebrow">LIVE LITERATURE RADAR</span>
    <h1>OPECT 研究，<em>实时</em>抵达。</h1>
    <p>自动聚合 OpenAlex、Crossref、PubMed 的有机光电化学晶体管及其光栅、沟道、异质结、生物传感与光电突触相关工作，按相关性排序，每天更新。</p>
    <div class="hero-actions">
      <button class="btn btn-primary" id="sync-btn-hero" type="button">{T.icon('refresh')}立即同步</button>
      <a class="btn btn-ghost" href="/papers?sort=relevance">浏览全部文献{T.icon('arrow')}</a>
    </div>
  </div>
  <div class="hero-panel">
    <div class="hero-panel-head"><span>近 21 天收录</span><span class="live-dot">LIVE</span></div>
    {spark}
    <div class="hero-panel-foot">
      <span>最新一条</span>
      <strong>{T.esc(T.short_date(recent[0]['published_date']) if recent else '—')}</strong>
    </div>
  </div>
</section>
<section class="stat-grid">{stats_row}</section>
<div class="split">
  <section class="feed">
    <div class="section-head">
      <h2>最新文献</h2>
      <a class="link" href="/papers">全部 {T.fmt_num(stats['total'])} 篇{T.icon('arrow')}</a>
    </div>
    <div class="paper-list">{feed}</div>
  </section>
  <aside class="rail">
    <div class="panel">
      <h3>热门主题</h3>
      <div class="topic-cloud">{topics_html}</div>
    </div>
    <div class="panel">
      <h3>高频期刊</h3>
      {venues_html}
      <a class="panel-more" href="/stats">查看统计{T.icon('arrow')}</a>
    </div>
    <div class="panel">
      <h3>高产作者</h3>
      {authors_html}
    </div>
    <div class="panel sync-panel">
      <h3>同步状态</h3>
      <div id="sync-detail">{sync_detail_html(sync, latest)}</div>
    </div>
  </aside>
</div>
"""
    return T.layout(
        title="实时追踪",
        body=body,
        active="stream",
        site=site,
        stats=stats,
    )


def venue_display_name(name: str | None) -> str:
    if not name:
        return "—"
    from .db import _venue_display

    return _venue_display(name)


def sync_time_label(value: str | None) -> str:
    """Human-friendly timestamp in the browser's local timezone (Asia/Shanghai)."""
    if not value:
        return "尚未同步"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone()
    except ValueError:
        return T.rel_date(value[:10])
    delta = (_today() - dt.date()).days
    clock = dt.strftime("%H:%M")
    if delta == 0:
        return f"今天 {clock}"
    if delta == 1:
        return f"昨天 {clock}"
    if delta < 7:
        return f"{delta} 天前"
    return dt.strftime("%m-%d %H:%M")


def sync_detail_html(sync: dict, last_run: dict | None = None) -> str:
    if not sync.get("stats") and not sync.get("running"):
        # Server restarted: fall back to the last recorded run in the database.
        if last_run:
            s = json.loads(last_run.get("stats_json") or "{}")
            return f"""<div class="sync-done">
  <div class="sync-line"><span>上次同步</span><b>{T.esc(sync_time_label(last_run.get('finished_at')))}</b></div>
  <div class="sync-line"><span>抓取</span><b>{T.fmt_num(s.get('fetched', 0))}</b></div>
  <div class="sync-line"><span>新增入库</span><b>{T.fmt_num(s.get('new', 0))}</b></div>
  <div class="sync-line"><span>相关文献</span><b>{T.fmt_num(s.get('relevant', 0))}</b></div>
</div>"""
        return '<p class="muted">尚未运行同步。点击「同步」开始抓取。</p>'
    if sync.get("running"):
        return f"""<div class="sync-live">
  <div class="sync-stage">{T.esc(sync.get('stage') or '同步中')}</div>
  <div class="progress"><span style="width:{sync.get('progress', 0)}%"></span></div>
  <div class="sync-nums">{T.esc(sync.get('progress', 0))}% · 已入库 {T.fmt_num((sync.get('stats') or {}).get('stored', 0))}</div>
</div>"""
    stats = sync.get("stats") or {}
    if stats:
        return f"""<div class="sync-done">
  <div class="sync-line"><span>本次抓取</span><b>{T.fmt_num(stats.get('fetched', 0))}</b></div>
  <div class="sync-line"><span>新增入库</span><b>{T.fmt_num(stats.get('new', 0))}</b></div>
  <div class="sync-line"><span>相关文献</span><b>{T.fmt_num(stats.get('relevant', 0))}</b></div>
  <div class="sync-line"><span>耗时</span><b>{T.esc(stats.get('duration_s', 0))} s</b></div>
</div>"""
    return '<p class="muted">尚未运行同步。点击「同步」开始抓取。</p>'


# -------------------------------------------------------------------- papers
def papers_page(db: Database, params: dict[str, Any]) -> str:
    cfg = load_config()
    site = cfg["site"]
    page = _int(params.get("page"), 1)
    q = (params.get("q") or "").strip()
    tier = params.get("tier") or ""
    year = _int(params.get("year"))
    topic = params.get("topic") or ""
    venue = params.get("venue") or ""
    source = params.get("source") or ""
    author = params.get("author") or ""
    sort = params.get("sort") or ("relevance" if q else "newest")
    favorite = params.get("favorite") in ("1", "true", "on")
    days = _int(params.get("days"))

    result = db.query_works(
        q=q,
        year_from=year if year else None,
        year_to=year if year else None,
        venue=venue,
        topic=topic,
        tier=tier,
        source=source,
        author=author,
        favorite=favorite,
        days=days,
        sort=sort,
        page=page,
        per_page=PAGE_SIZE,
        min_relevance=float(cfg["relevance"].get("include_threshold", 30)),
    )
    facets = db.facets()
    current = {
        "q": q,
        "tier": tier,
        "year": year,
        "topic": topic,
        "venue": venue,
        "source": source,
        "author": author,
        "sort": sort,
        "favorite": "1" if favorite else "",
        "days": days,
    }

    def facet_group(title: str, key: str, rows: list[tuple[str, int]], limit: int = 12) -> str:
        items = []
        for label, count in rows[:limit]:
            active = " active" if str(current.get(key) or "") == str(label) else ""
            items.append(
                f'<a class="facet-item{active}" href="{T.filter_url(current, {key: label, "page": 1})}">'
                f'<span>{T.esc(label)}</span><b>{T.fmt_num(count)}</b></a>'
            )
        is_open = bool(current.get(key))
        return (
            f'<details class="facet"{" open" if is_open else ""}>'
            f'<summary>{T.esc(title)}</summary><div class="facet-list">{"".join(items)}</div></details>'
        )

    tiers = [("core", "核心 OPECT"), ("related", "强相关"), ("peripheral", "外围相关")]
    tier_rows = [(label, db._conn().execute("SELECT count(*) FROM works WHERE tier=? AND relevance>=30", (key,)).fetchone()[0]) for key, label in tiers]
    year_rows = [(str(y["year"]), y["count"]) for y in facets["years"]]
    topic_rows = facets["topics"]
    venue_rows = [(v["venue"], v["count"]) for v in facets["venues"]]
    source_counts = {k: v for k, v in (db.stats().get("sources") or {}).items()}
    source_rows = [
        (label, source_counts.get(key, 0))
        for key, label in [("openalex", "OpenAlex"), ("crossref", "Crossref"), ("pubmed", "PubMed"), ("arxiv", "arXiv")]
    ]

    cards = "".join(T.paper_card(w) for w in result["items"]) or T.empty_state(
        "没有匹配的文献", "尝试放宽筛选条件，或更换关键词。"
    )
    chips = T.filter_chips({}, facets, current)
    pag = T.pagination(result["page"], result["pages"], current)
    sort_options = [
        ("newest", "最新发表"),
        ("relevance", "相关性"),
        ("citations", "被引最多"),
        ("oldest", "最早发表"),
    ]
    sort_html = "".join(
        f'<a class="sort-opt{" active" if sort == k else ""}" href="{T.filter_url(current, {"sort": k, "page": 1})}">{label}</a>'
        for k, label in sort_options
    )
    body = f"""
<div class="page-head">
  <div>
    <span class="eyebrow">LIBRARY</span>
    <h1>全部文献</h1>
    <p>共 <strong>{T.fmt_num(result['total'])}</strong> 篇匹配文献{('，关键词「' + T.esc(q) + '」') if q else ''}。</p>
  </div>
  <div class="page-actions">
    <div class="segmented">{sort_html}</div>
    <div class="export-menu">
      <button class="btn btn-ghost" type="button" data-export-toggle>{T.icon('download')}导出</button>
      <div class="export-pop" hidden>
        <a href="/export.csv?{urlencode({k: v for k, v in current.items() if v})}">CSV 表格</a>
        <a href="/export.bib?{urlencode({k: v for k, v in current.items() if v})}">BibTeX</a>
        <a href="/export.json?{urlencode({k: v for k, v in current.items() if v})}">JSON</a>
      </div>
    </div>
  </div>
</div>
<div class="library">
  <aside class="facets">
    <div class="facet-title">{T.icon('filter')}筛选</div>
    {facet_group("相关性层级", "tier", tier_rows)}
    {facet_group("发表年份", "year", year_rows)}
    {facet_group("研究主题", "topic", topic_rows, 14)}
    {facet_group("期刊 / 会议", "venue", venue_rows, 14)}
    {facet_group("数据来源", "source", source_rows)}
    <a class="facet-item {'active' if favorite else ''}" href="{T.filter_url(current, {'favorite': '' if favorite else '1', 'page': 1})}">
      <span class="fav-facet">{T.icon('star')}仅看收藏</span><b>{db.stats().get('favorites', 0)}</b>
    </a>
  </aside>
  <section class="results">
    {chips}
    <div class="result-meta"><span>第 {result['page']} / {result['pages']} 页</span><span>{T.fmt_num(result['total'])} 篇</span></div>
    <div class="paper-list">{cards}</div>
    {pag}
  </section>
</div>
"""
    return T.layout(title="全部文献", body=body, active="papers", site=site, stats=db.stats(), wide=True)


# ---------------------------------------------------------------------- paper
def paper_page(db: Database, work_id: int) -> str | None:
    work = db.get_work(work_id)
    if not work:
        return None
    cfg = load_config()
    site = cfg["site"]
    authors = work.get("authors") or []
    author_html = "".join(
        f'<a class="author-chip" href="/papers?author={T.esc(a.get("name", ""))}">{T.esc(a.get("name", ""))}</a>'
        for a in authors
    ) or '<span class="muted">作者信息缺失</span>'
    topics = "".join(
        f'<a class="tag" href="/papers?topic={T.esc(t)}">{T.esc(t)}</a>' for t in work.get("topics") or []
    )
    hits = "".join(
        f'<li><b>{T.esc(h.get("label"))}</b><span>{T.esc(h.get("weight"))} 分</span>'
        f'<em>{"标题" if h.get("title") else ""}{" · " if h.get("title") and h.get("abstract") else ""}{"摘要" if h.get("abstract") else ""}</em></li>'
        for h in (work.get("hits") or [])[:8]
    )
    related = []
    if work.get("topics"):
        for topic in work["topics"][:2]:
            for w in db.query_works(topic=topic, per_page=6, sort="relevance")["items"]:
                if w["id"] != work["id"] and all(r["id"] != w["id"] for r in related):
                    related.append(w)
    related = related[:5]
    related_html = "".join(T.paper_card(w, compact=True) for w in related)
    doi = work.get("doi")
    doi_url = f"https://doi.org/{doi}" if doi else ""
    links = []
    used_urls: set[str] = set()
    if doi_url:
        links.append(f'<a class="btn btn-primary" href="{T.esc(doi_url)}" target="_blank" rel="noopener">{T.icon("external")}阅读原文</a>')
        used_urls.add(doi_url.lower())
    for label, url in (("PDF", work.get("pdf_url") or work.get("oa_url")), ("来源页面", work.get("landing_url"))):
        if url and url.lower() not in used_urls:
            used_urls.add(url.lower())
            links.append(f'<a class="btn btn-ghost" href="{T.esc(url)}" target="_blank" rel="noopener">{T.icon("download")}{label}</a>')
    meta_rows = [
        ("期刊 / 会议", venue_display_name(work.get("venue"))),
        ("发表时间", T.short_date(work.get("published_date"))),
        ("文献类型", work.get("work_type") or "—"),
        ("出版商", work.get("publisher") or "—"),
        ("被引次数", str(work.get("cited_by") or 0)),
        ("开放获取", "是" if work.get("is_open_access") else "否"),
        ("DOI", doi or "—"),
        ("数据来源", ", ".join(work.get("sources") or [])),
    ]
    meta_html = "".join(
        f'<div class="meta-row"><dt>{T.esc(k)}</dt><dd>{T.esc(v)}</dd></div>' for k, v in meta_rows
    )
    abstract = work.get("abstract") or "该来源未提供摘要。可点击「阅读原文」查看出版页面。"
    tier_text, tier_cls = T.tier_label(work.get("tier", ""))
    body = f"""
<nav class="crumbs"><a href="/">最新</a><span>/</span><a href="/papers">全部文献</a><span>/</span><span>详情</span></nav>
<article class="detail">
  <header class="detail-head">
    <div class="detail-badges">
      <span class="score {T.score_class(float(work.get('relevance') or 0))}">{T.esc(round(float(work.get('relevance') or 0)))}<small>分</small></span>
      <span class="tier {tier_cls}">{T.esc(tier_text)}</span>
      <span class="muted">{T.esc(work.get('relevance_reason'))}</span>
    </div>
    <h1>{T.esc(work.get('title'))}</h1>
    <div class="detail-authors">{author_html}</div>
    <div class="detail-tags">{topics}</div>
    <div class="detail-actions">{''.join(links)}
      <button class="btn btn-ghost fav{' active' if work.get('favorite') else ''}" data-fav="{work['id']}" type="button">{T.icon('star')}<span>{'已收藏' if work.get('favorite') else '收藏'}</span></button>
    </div>
  </header>
  <div class="detail-grid">
    <section class="detail-main">
      <h2>摘要</h2>
      <p class="abstract-full">{T.esc(abstract)}</p>
      <h2>为什么被收录</h2>
      <p class="muted">系统按标题、摘要、关键词的命中情况计算相关性，核心概念命中会显著加权。</p>
      <ul class="hit-list">{hits or '<li class="muted">无命中记录</li>'}</ul>
      <h2>我的笔记</h2>
      <textarea class="note-box" id="note-box" data-note="{work['id']}" rows="5" placeholder="记录阅读要点、可复现实验条件、可引用的结论…">{T.esc(work.get('note') or '')}</textarea>
      <div class="note-actions"><button class="btn btn-primary" id="note-save" type="button">保存笔记</button><span class="muted" id="note-status"></span></div>
    </section>
    <aside class="detail-side">
      <div class="panel">
        <h3>文献信息</h3>
        <dl class="meta-list">{meta_html}</dl>
      </div>
      <div class="panel">
        <h3>主题</h3>
        <div class="topic-cloud">{topics or '<span class="muted">—</span>'}</div>
      </div>
    </aside>
  </div>
</article>
<section class="related">
  <div class="section-head"><h2>相关文献</h2><a class="link" href="/papers?sort=relevance">更多{T.icon('arrow')}</a></div>
  <div class="paper-list">{related_html or '<p class="muted">暂无相关文献</p>'}</div>
</section>
"""
    return T.layout(title=work["title"][:60], body=body, active="papers", site=site, stats=db.stats(), wide=True)


# ------------------------------------------------------------------- timeline
def timeline_page(db: Database, params: dict[str, Any]) -> str:
    cfg = load_config()
    site = cfg["site"]
    tier = params.get("tier") or ""
    groups = db.timeline(tier=tier)
    total = sum(len(g["items"]) for g in groups)
    blocks = []
    for g in groups:
        month = g["month"]
        label = month
        try:
            y, m = month.split("-")
            label = f"{y} 年 {int(m)} 月"
        except ValueError:
            label = "日期未知"
        cards = "".join(T.paper_card(w, compact=True) for w in g["items"][:8])
        more = ""
        if len(g["items"]) > 8:
            more = f'<a class="link" href="/papers?year={month[:4]}&sort=newest">该月共 {len(g["items"])} 篇，查看全部{T.icon("arrow")}</a>'
        blocks.append(
            f"""<section class="tl-group">
  <div class="tl-marker"><span>{T.esc(label)}</span><b>{len(g['items'])}</b></div>
  <div class="tl-body"><div class="paper-list">{cards}</div>{more}</div>
</section>"""
        )
    filter_bar = "".join(
        f'<a class="seg{" active" if tier == k else ""}" href="/timeline{("?tier=" + k) if k else ""}">{label}</a>'
        for k, label in [("", "全部"), ("core", "核心 OPECT"), ("related", "强相关")]
    )
    body = f"""
<div class="page-head">
  <div>
    <span class="eyebrow">TIMELINE</span>
    <h1>研究时间线</h1>
    <p>按发表月份倒序排列，共 <strong>{T.fmt_num(total)}</strong> 篇文献。</p>
  </div>
  <div class="segmented">{filter_bar}</div>
</div>
<div class="timeline">{''.join(blocks) or T.empty_state('暂无数据', '先运行一次同步。')}</div>
"""
    return T.layout(title="时间线", body=body, active="timeline", site=site, stats=db.stats(), wide=True)


# ---------------------------------------------------------------------- stats
def stats_page(db: Database) -> str:
    cfg = load_config()
    site = cfg["site"]
    stats = db.stats()
    daily = daily_counts(db, 30)
    months = monthly_counts(db, 24)
    trend = T.line_chart([(k[5:], v) for k, v in months], width=780, height=250)
    venue_rows = [(v["venue"], v["count"]) for v in stats["venues"]]
    topic_rows = stats["topics"]
    year_rows = [(str(y["year"]), y["count"]) for y in stats["years"] if y["year"]]
    source_rows = list(stats["sources"].items())
    source_label = {"openalex": "OpenAlex", "crossref": "Crossref", "pubmed": "PubMed", "arxiv": "arXiv"}
    source_rows = [(source_label.get(k, k), v) for k, v in source_rows]
    author_rows = [(a["name"], a["count"]) for a in stats["authors"]]
    avg_score = db._conn().execute("SELECT avg(relevance) FROM works WHERE relevance>=30").fetchone()[0] or 0
    recent_7 = sum(v for _, v in daily[-7:])
    prev_7 = sum(v for _, v in daily[-14:-7])
    delta = recent_7 - prev_7
    delta_txt = f"{'+' if delta >= 0 else ''}{delta}"
    body = f"""
<div class="page-head">
  <div>
    <span class="eyebrow">ANALYTICS</span>
    <h1>统计分析</h1>
    <p>基于 {T.fmt_num(stats['total'])} 篇相关文献的发表趋势、期刊与主题分布。</p>
  </div>
</div>
<section class="stat-grid">
  {T.stat_card('相关文献总量', T.fmt_num(stats['total']), '相关性 ≥ 30 分', 'accent')}
  {T.stat_card('近 7 天新增', T.fmt_num(recent_7), f'较前 7 天 {delta_txt}', '')}
  {T.stat_card('核心 OPECT', T.fmt_num(stats['core']), f"占比 {round(100 * stats['core'] / max(1, stats['total']))}%", '')}
  {T.stat_card('平均相关性', f"{avg_score:.0f}", '收录文献平均分', '')}
</section>
<div class="chart-grid">
  <section class="panel chart-wide">
    <div class="panel-head"><h3>月度发表趋势</h3><span class="muted">最近 24 个月</span></div>
    {trend}
  </section>
  <section class="panel">
    <div class="panel-head"><h3>年度分布</h3></div>
    {T.bar_chart(year_rows, accent='var(--c2)')}
  </section>
  <section class="panel">
    <div class="panel-head"><h3>主题构成</h3></div>
    {T.donut(topic_rows, center_total=stats['total'])}
  </section>
  <section class="panel">
    <div class="panel-head"><h3>高频期刊</h3></div>
    {T.bar_chart(venue_rows, accent='var(--c3)')}
  </section>
  <section class="panel">
    <div class="panel-head"><h3>高产作者</h3></div>
    {T.bar_chart(author_rows, accent='var(--c4)')}
  </section>
  <section class="panel">
    <div class="panel-head"><h3>数据来源覆盖</h3></div>
    {T.donut(source_rows, size=168)}
    <p class="muted small">同一文献可能同时出现在多个来源，入库时自动合并。</p>
  </section>
</div>
"""
    return T.layout(title="统计分析", body=body, active="stats", site=site, stats=stats, wide=True)


# -------------------------------------------------------------------- settings
def settings_page(db: Database, engine: SyncEngine, message: str = "") -> str:
    cfg = load_config()
    site = cfg["site"]
    sync = cfg.get("sync", {})
    rel = cfg.get("relevance", {})
    sources = sync.get("sources", {})
    syncs = db.list_syncs(10)
    runs = "".join(
        f'<tr><td>#{r["id"]}</td><td>{T.esc((r.get("started_at") or "")[:19].replace("T", " "))}</td>'
        f'<td><span class="badge {r["status"]}">{T.esc(r["status"])}</span></td>'
        f'<td>{T.esc(json.loads(r.get("stats_json") or "{}").get("new", 0))}</td>'
        f'<td>{T.esc(json.loads(r.get("stats_json") or "{}").get("relevant", 0))}</td>'
        f'<td class="muted">{T.esc(r.get("message") or "")}</td></tr>'
        for r in syncs
    )
    source_toggles = "".join(
        f'<label class="switch"><input type="checkbox" name="source_{k}" {"checked" if v else ""}>'
        f'<span></span>{label}</label>'
        for k, label in [("openalex", "OpenAlex"), ("crossref", "Crossref"), ("pubmed", "PubMed"), ("arxiv", "arXiv")]
        for v in [sources.get(k, False)]
    )
    keyword_groups = "".join(
        f'<div class="kw-group"><div class="kw-head"><strong>{T.esc(g["label"])}</strong><span>权重 {g["weight"]}</span></div>'
        f'<div class="kw-list">{"".join(f"<code>{T.esc(p)}</code>" for p in g.get("phrases", []))}</div></div>'
        for g in cfg.get("keywords", [])
    )
    body = f"""
<div class="page-head">
  <div><span class="eyebrow">SETTINGS</span><h1>设置</h1><p>调整同步频率、数据源与相关性阈值。</p></div>
</div>
{f'<div class="flash success">{T.esc(message)}</div>' if message else ''}
<div class="settings-grid">
  <section class="panel">
    <div class="panel-head"><h3>同步配置</h3></div>
    <form method="post" action="/settings" class="form">
      <input type="hidden" name="form" value="sync">
      <label class="field"><span>自动同步间隔（分钟）</span>
        <input type="number" name="interval_minutes" min="15" max="10080" value="{T.esc(sync.get('interval_minutes', 360))}">
      </label>
      <label class="field"><span>回溯天数（首次同步）</span>
        <input type="number" name="initial_lookback_days" min="30" max="7300" value="{T.esc(sync.get('initial_lookback_days', 3650))}">
      </label>
      <label class="field"><span>联系邮箱（用于 OpenAlex / Crossref 礼貌池）</span>
        <input type="email" name="contact_email" value="{T.esc(sync.get('contact_email', ''))}" placeholder="you@example.com">
      </label>
      <div class="field"><span>数据源</span><div class="switches">{source_toggles}</div></div>
      <label class="switch"><input type="checkbox" name="auto_sync" {"checked" if sync.get("auto_sync", True) else ""}><span></span>启用后台自动同步</label>
      <label class="switch"><input type="checkbox" name="on_start" {"checked" if sync.get("on_start", True) else ""}><span></span>启动时若数据过期则自动抓取</label>
      <button class="btn btn-primary" type="submit">{T.icon('check')}保存设置</button>
    </form>
  </section>
  <section class="panel">
    <div class="panel-head"><h3>相关性阈值</h3></div>
    <form method="post" action="/settings" class="form">
      <input type="hidden" name="form" value="relevance">
      <label class="field"><span>收录阈值（≥ 该分进入列表）</span>
        <input type="number" name="include_threshold" min="0" max="100" value="{T.esc(rel.get('include_threshold', 30))}">
      </label>
      <label class="field"><span>核心阈值（≥ 该分标记核心 OPECT）</span>
        <input type="number" name="core_threshold" min="0" max="100" value="{T.esc(rel.get('core_threshold', 70))}">
      </label>
      <button class="btn btn-primary" type="submit">{T.icon('check')}更新阈值</button>
      <p class="muted small">调整后不会重算历史文献；可点击下方「重新打分」立即应用。</p>
    </form>
    <div class="panel-head" style="margin-top:18px"><h3>重新计算相关性</h3></div>
    <p class="muted small">按当前关键词与阈值重算全库，并清理低于收录线的条目。</p>
    <button class="btn btn-ghost" type="button" id="rescore-btn">{T.icon('refresh')}重新打分</button>
  </section>
  <section class="panel">
    <div class="panel-head"><h3>数据导出</h3></div>
    <p class="muted small">导出当前收录的全部文献（相关性 ≥ 30 分），可直接导入 Zotero / EndNote。</p>
    <div class="export-row">
      <a class="btn btn-ghost" href="/export.csv">{T.icon('download')}CSV</a>
      <a class="btn btn-ghost" href="/export.bib">{T.icon('download')}BibTeX</a>
      <a class="btn btn-ghost" href="/export.json">{T.icon('download')}JSON</a>
    </div>
    <div class="panel-head" style="margin-top:18px"><h3>数据备份</h3></div>
    <p class="muted small">数据库为单文件 <code>data/opect.db</code>，复制该文件即完成备份。</p>
  </section>
  <section class="panel span-2">
    <div class="panel-head"><h3>检索关键词</h3><span class="muted">编辑 config.json 后重启生效</span></div>
    <div class="kw-groups">{keyword_groups}</div>
  </section>
  <section class="panel span-2">
    <div class="panel-head"><h3>同步历史</h3></div>
    <div class="table-wrap"><table class="table">
      <thead><tr><th>#</th><th>开始时间</th><th>状态</th><th>新增</th><th>相关</th><th>说明</th></tr></thead>
      <tbody>{runs or '<tr><td colspan="6" class="muted">暂无记录</td></tr>'}</tbody>
    </table></div>
  </section>
</div>
"""
    return T.layout(title="设置", body=body, active="settings", site=site, stats=db.stats(), wide=True)


# ---------------------------------------------------------------------- utils
def _int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
