"""HTML templates for the OPECT literature radar (server-rendered, no deps)."""

from __future__ import annotations

import html
import json
import math
import re
from datetime import date, datetime
from typing import Any, Iterable

E = html.escape


def esc(value: Any) -> str:
    return E("" if value is None else str(value), quote=True)


def js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def fmt_num(n: int | float | None) -> str:
    if n is None:
        return "0"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def short_date(value: str | None) -> str:
    if not value:
        return "日期未知"
    try:
        d = date.fromisoformat(value[:10])
    except ValueError:
        return value
    return f"{d.year}.{d.month:02d}.{d.day:02d}"


def rel_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        d = date.fromisoformat(value[:10])
    except ValueError:
        return value
    today = date.today()
    if d > today:
        return "即将发表"
    delta = (today - d).days
    if delta < 0:
        return "即将发表"
    if delta == 0:
        return "今天"
    if delta == 1:
        return "昨天"
    if delta < 7:
        return f"{delta} 天前"
    if delta < 60:
        return f"{delta // 7} 周前"
    if delta < 365:
        return f"{delta // 30} 个月前"
    return f"{delta // 365} 年前"


def authors_line(authors: list[dict], limit: int = 4) -> str:
    names = [a.get("name", "") for a in authors if a.get("name")]
    if not names:
        return "作者信息缺失"
    if len(names) <= limit:
        return " · ".join(names)
    return " · ".join(names[:limit]) + f" 等 {len(names)} 人"


def _clean_venue(name: str | None) -> str:
    """Lightweight display normalisation for venue names in cards."""
    if not name:
        return ""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name.strip())
    words = name.split()
    if len(words) > 1 and not any(w.isupper() for w in words[1:]):
        name = name[:1].upper() + name[1:]
    small = {"and", "of", "the", "in", "on", "for", "a", "an", "to", "with"}
    return " ".join(
        w.upper() if (w.isupper() and len(w) > 1) else (w.lower() if i and w.lower() in small else w[:1].upper() + w[1:].lower())
        for i, w in enumerate(words)
    )


def tier_label(tier: str) -> tuple[str, str]:
    return {
        "core": ("核心 OPECT", "core"),
        "related": ("强相关", "related"),
        "peripheral": ("外围相关", "peripheral"),
    }.get(tier, ("弱相关", "peripheral"))


def score_class(score: float) -> str:
    if score >= 85:
        return "s-core"
    if score >= 70:
        return "s-high"
    if score >= 50:
        return "s-mid"
    return "s-low"


# --------------------------------------------------------------------- icons
ICONS = {
    "radar": '<path d="M12 3a9 9 0 1 0 9 9"/><path d="M12 12l6.5-6.5"/><path d="M12 3v9h9"/><circle cx="12" cy="12" r="2.2"/>',
    "stream": '<path d="M4 6h16M4 12h10M4 18h13"/>',
    "list": '<path d="M8 6h12M8 12h12M8 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "star": '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3 6.4 20.2l1.1-6.2L3 9.6l6.2-.9z"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/>',
    "download": '<path d="M12 3v12"/><path d="m7 11 5 5 5-5"/><path d="M4 21h16"/>',
    "arrow": '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    "external": '<path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5"/>',
    "book": '<path d="M4 5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 1-2-2z"/><path d="M8 3v18"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "filter": '<path d="M3 5h18l-7 8v6l-4 2v-8z"/>',
    "check": '<path d="m4 12 5 5L20 6"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 12.8A8.5 8.5 0 1 1 11.2 3a6.6 6.6 0 0 0 9.8 9.8z"/>',
    "quote": '<path d="M7 7h4v6H7a2 2 0 0 0 2 2v2a4 4 0 0 1-4-4V9a2 2 0 0 1 2-2zM16 7h4v6h-4a2 2 0 0 0 2 2v2a4 4 0 0 1-4-4V9a2 2 0 0 1 2-2z"/>',
}


def icon(name: str, cls: str = "") -> str:
    path = ICONS.get(name, "")
    return (
        f'<svg class="ic {cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{path}</svg>'
    )


# -------------------------------------------------------------------- layout
def layout(
    *,
    title: str,
    body: str,
    active: str = "",
    site: dict,
    stats: dict | None = None,
    wide: bool = False,
    body_class: str = "",
) -> str:
    nav_items = [
        ("stream", "/", "最新文献", "stream"),
        ("papers", "/papers", "全部文献", "list"),
        ("timeline", "/timeline", "时间线", "clock"),
        ("stats", "/stats", "统计分析", "chart"),
        ("settings", "/settings", "设置", "gear"),
    ]
    nav = "".join(
        f'<a class="nav-item{" active" if active == key else ""}" href="{href}">'
        f'{icon(ic)}<span>{label}</span></a>'
        for key, href, label, ic in nav_items
    )
    total = fmt_num((stats or {}).get("total", 0))
    return f"""<!doctype html>
<html lang="{esc(site.get('lang', 'zh-CN'))}" data-theme="auto">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="OPECT 有机光电化学晶体管文献实时追踪：自动抓取、相关性评分、检索筛选与趋势分析。">
<title>{esc(title)} · {esc(site.get('title', 'OPECT 文献雷达'))}</title>
<link rel="stylesheet" href="/static/style.css?v=1">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<script>
  (function() {{
    var t = localStorage.getItem('opect-theme') || 'auto';
    document.documentElement.setAttribute('data-theme', t);
  }})();
</script>
</head>
<body class="{esc(body_class)}">
<div class="app">
  <aside class="sidebar">
    <a class="brand" href="/">
      <span class="brand-mark">{icon('radar')}</span>
      <span class="brand-text"><strong>OPECT</strong><small>文献雷达</small></span>
    </a>
    <nav class="nav">{nav}</nav>
    <div class="side-foot">
      <div class="side-stat"><span>追踪文献</span><strong id="side-total">{total}</strong></div>
      <div class="side-sync" id="side-sync">等待同步</div>
      <button class="theme-toggle" id="theme-toggle" type="button" aria-label="切换主题">
        <span class="theme-sun">{icon('sun')}</span><span class="theme-moon">{icon('moon')}</span>
        <span class="theme-label">跟随系统</span>
      </button>
    </div>
  </aside>
  <div class="main">
    <header class="topbar">
      <button class="menu-btn" id="menu-btn" type="button" aria-label="菜单">{icon('stream')}</button>
      <form class="search" role="search" action="/papers" method="get">
        {icon('search')}
        <input type="search" name="q" id="global-search" placeholder="搜索标题、摘要、作者…  ( / )" autocomplete="off">
        <kbd>/</kbd>
      </form>
      <div class="topbar-actions">
        <button class="btn btn-ghost" id="sync-btn" type="button" title="立即抓取最新文献">
          {icon('refresh')}<span>同步</span>
        </button>
      </div>
    </header>
    <div class="sync-banner" id="sync-banner" hidden></div>
    <main class="content{' wide' if wide else ''}">{body}</main>
    <footer class="foot">
      <span>{esc(site.get('title', 'OPECT 文献雷达'))} · 数据来自 OpenAlex / Crossref / PubMed</span>
      <span>仅作科研信息聚合，请以原文为准</span>
    </footer>
  </div>
</div>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script src="/static/app.js?v=1" defer></script>
</body>
</html>"""


# --------------------------------------------------------------------- pieces
def stat_card(label: str, value: str, hint: str = "", tone: str = "") -> str:
    return f"""<div class="stat-card {tone}">
  <span class="stat-label">{esc(label)}</span>
  <strong class="stat-value">{esc(value)}</strong>
  <span class="stat-hint">{esc(hint)}</span>
</div>"""


def paper_card(w: dict, *, compact: bool = False) -> str:
    tier_text, tier_cls = tier_label(w.get("tier", ""))
    topics = w.get("topics") or []
    tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in topics[:4])
    authors = authors_line(w.get("authors") or [])
    abstract = (w.get("abstract") or "").strip()
    snippet = abstract[:260] + ("…" if len(abstract) > 260 else "")
    doi = w.get("doi")
    doi_url = f"https://doi.org/{esc(doi)}" if doi else ""
    pdf = w.get("pdf_url") or w.get("oa_url")
    venue = _clean_venue(w.get("venue")) or "预印本 / 未标注期刊"
    reason = w.get("relevance_reason") or ""
    oa = '<span class="oa">开放获取</span>' if w.get("is_open_access") else ""
    actions = []
    if doi:
        actions.append(
            f'<a class="act" href="{doi_url}" target="_blank" rel="noopener">{icon("external")}DOI</a>'
        )
    if pdf:
        actions.append(
            f'<a class="act" href="{esc(pdf)}" target="_blank" rel="noopener">{icon("download")}PDF</a>'
        )
    actions.append(
        f'<a class="act" href="/paper/{w["id"]}">{icon("book")}详情</a>'
    )
    fav_cls = " active" if w.get("favorite") else ""
    return f"""<article class="paper{' compact' if compact else ''}" data-id="{w['id']}">
  <div class="paper-main">
    <div class="paper-top">
      <span class="score {score_class(float(w.get('relevance') or 0))}" title="相关性评分：{esc(w.get('relevance'))}">
        {esc(round(float(w.get('relevance') or 0)))}<small>分</small>
      </span>
      <div class="paper-meta">
        <span class="tier {tier_cls}">{esc(tier_text)}</span>
        <span class="dot"></span>
        <span>{esc(venue)}</span>
        <span class="dot"></span>
        <time datetime="{esc(w.get('published_date') or '')}" title="{esc(short_date(w.get('published_date')))}">{esc(rel_date(w.get('published_date')))}</time>
        {oa}
      </div>
    </div>
    <h3 class="paper-title"><a href="/paper/{w['id']}">{esc(w.get('title'))}</a></h3>
    <p class="paper-authors">{esc(authors)}</p>
    <p class="paper-abstract">{esc(snippet)}</p>
    <div class="paper-tags">{tags}<span class="reason">{esc(reason)}</span></div>
    <div class="paper-actions">
      {''.join(actions)}
      <span class="spacer"></span>
      <span class="cites" title="被引次数">{icon('quote')}{fmt_num(w.get('cited_by') or 0)}</span>
      <button class="act fav{fav_cls}" type="button" data-fav="{w['id']}" title="收藏">{icon('star')}<span>收藏</span></button>
    </div>
  </div>
</article>"""


def empty_state(title: str, hint: str, action: str = "") -> str:
    return f"""<div class="empty">
  <div class="empty-mark">{icon('radar')}</div>
  <h3>{esc(title)}</h3>
  <p>{esc(hint)}</p>
  {action}
</div>"""


# ------------------------------------------------------------------ charts
def sparkline(values: list[float], width: int = 220, height: int = 46, cls: str = "") -> str:
    if not values:
        return ""
    mx = max(values) or 1
    n = len(values)
    step = width / max(1, n - 1)
    pts = [(i * step, height - (v / mx) * (height - 6) - 3) for i, v in enumerate(values)]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = path + f" L{width:.1f},{height} L0,{height} Z"
    return (
        f'<svg class="spark {cls}" viewBox="0 0 {width} {height}" preserveAspectRatio="none" aria-hidden="true">'
        f'<path class="spark-area" d="{area}"/><path class="spark-line" d="{path}"/></svg>'
    )


def bar_chart(
    rows: list[tuple[str, int]],
    *,
    height: int = 190,
    accent: str = "var(--accent)",
    max_bars: int = 12,
) -> str:
    if not rows:
        return '<p class="muted">暂无数据</p>'
    rows = rows[:max_bars]
    mx = max(v for _, v in rows) or 1
    bars = []
    for label, value in rows:
        pct = max(2, round(100 * value / mx))
        bars.append(
            f'<div class="bar-row"><span class="bar-label" title="{esc(label)}">{esc(label)}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{pct}%;background:{accent}"></span></span>'
            f'<span class="bar-value">{fmt_num(value)}</span></div>'
        )
    return f'<div class="bar-chart" style="--bar-h:{height}px">' + "".join(bars) + "</div>"


def line_chart(rows: list[tuple[str, int]], *, width: int = 760, height: int = 240) -> str:
    if not rows:
        return '<p class="muted">暂无数据</p>'
    mx = max(v for _, v in rows) or 1
    n = len(rows)
    pad_l, pad_r, pad_t, pad_b = 38, 16, 16, 30
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b
    pts = []
    for i, (_, v) in enumerate(rows):
        x = pad_l + (iw * i / max(1, n - 1))
        y = pad_t + ih - (v / mx) * ih
        pts.append((x, y))
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = line + f" L{pts[-1][0]:.1f},{pad_t + ih} L{pts[0][0]:.1f},{pad_t + ih} Z"
    grid = []
    for i in range(4):
        y = pad_t + ih * i / 3
        val = round(mx * (3 - i) / 3)
        grid.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{pad_l - 8}" y="{y + 4:.1f}" class="axis" text-anchor="end">{val}</text>'
        )
    labels = []
    every = max(1, math.ceil(n / 8))
    for i, (label, _) in enumerate(rows):
        if i % every == 0 or i == n - 1:
            x = pts[i][0]
            labels.append(
                f'<text x="{x:.1f}" y="{height - 8}" class="axis" text-anchor="middle">{esc(label)}</text>'
            )
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" class="dot-mark"><title>{esc(rows[i][0])}: {rows[i][1]}</title></circle>'
        for i, (x, y) in enumerate(pts)
    )
    return (
        f'<svg class="line-chart" viewBox="0 0 {width} {height}" role="img">'
        + "".join(grid)
        + f'<path class="area" d="{area}"/><path class="line" d="{line}"/>{dots}'
        + "".join(labels)
        + "</svg>"
    )


def donut(rows: list[tuple[str, int]], size: int = 168, center_total: int | None = None) -> str:
    total = sum(v for _, v in rows) or 1
    center = center_total if center_total is not None else total
    colors = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)"]
    r, cx, cy = size / 2 - 12, size / 2, size / 2
    circ = 2 * math.pi * r
    offset = 0.0
    segs = []
    legend = []
    for i, (label, value) in enumerate(rows[:6]):
        frac = value / total
        dash = frac * circ
        color = colors[i % len(colors)]
        segs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="18" '
            f'stroke-dasharray="{dash:.2f} {circ - dash:.2f}" stroke-dashoffset="{-offset:.2f}" '
            f'transform="rotate(-90 {cx} {cy})"><title>{esc(label)}: {value}</title></circle>'
        )
        legend.append(
            f'<li><i style="background:{color}"></i><span>{esc(label)}</span><b>{value}</b></li>'
        )
        offset += dash
    return (
        f'<div class="donut-wrap"><svg viewBox="0 0 {size} {size}" class="donut">'
        + "".join(segs)
        + f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" class="donut-num">{center}</text>'
        + f'<text x="{cx}" y="{cy + 16}" text-anchor="middle" class="donut-label">篇</text>'
        + "</svg>"
        + f'<ul class="donut-legend">{"".join(legend)}</ul></div>'
    )


# -------------------------------------------------------------------- filters
def filter_chips(params: dict[str, Any], facets: dict, current: dict) -> str:
    chips = []
    for key, label in (("tier", "层级"), ("year", "年份"), ("topic", "主题"), ("venue", "期刊")):
        val = current.get(key)
        if not val:
            continue
        chips.append(
            f'<a class="chip active" href="{filter_url(current, {key: None, "page": 1})}">'
            f'<span>{esc(label)}</span>{esc(val)}{icon("close")}</a>'
        )
    if current.get("q"):
        chips.append(
            f'<a class="chip active" href="{filter_url(current, {"q": None, "page": 1})}">'
            f'<span>检索</span>{esc(current["q"])}{icon("close")}</a>'
        )
    if current.get("favorite"):
        chips.append(
            f'<a class="chip active" href="{filter_url(current, {"favorite": None, "page": 1})}">'
            f'<span>仅收藏</span>{icon("close")}</a>'
        )
    if not chips:
        return ""
    return (
        '<div class="chips">'
        + "".join(chips)
        + f'<a class="chip clear" href="/papers">清除全部</a></div>'
    )


def filter_url(current: dict, overrides: dict) -> str:
    from urllib.parse import urlencode

    merged = {**current, **overrides}
    clean = {k: v for k, v in merged.items() if v not in (None, "", 0, False) and k != "page"}
    if "page" in overrides and overrides["page"]:
        clean["page"] = overrides["page"]
    return "/papers" + ("?" + urlencode(clean) if clean else "")


def pagination(page: int, pages: int, current: dict) -> str:
    if pages <= 1:
        return ""
    parts = []
    if page > 1:
        parts.append(
            f'<a class="page-btn" href="{filter_url(current, {"page": page - 1})}">上一页</a>'
        )
    start = max(1, page - 2)
    end = min(pages, page + 2)
    if start > 1:
        parts.append(f'<a class="page-btn" href="{filter_url(current, {"page": 1})}">1</a>')
        if start > 2:
            parts.append('<span class="page-gap">…</span>')
    for p in range(start, end + 1):
        cls = "page-btn current" if p == page else "page-btn"
        parts.append(f'<a class="{cls}" href="{filter_url(current, {"page": p})}">{p}</a>')
    if end < pages:
        if end < pages - 1:
            parts.append('<span class="page-gap">…</span>')
        parts.append(f'<a class="page-btn" href="{filter_url(current, {"page": pages})}">{pages}</a>')
    if page < pages:
        parts.append(
            f'<a class="page-btn" href="{filter_url(current, {"page": page + 1})}">下一页</a>'
        )
    return '<nav class="pagination">' + "".join(parts) + "</nav>"
