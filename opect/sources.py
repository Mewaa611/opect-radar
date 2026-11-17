"""Literature source adapters: OpenAlex, Crossref, PubMed, arXiv.

Every adapter yields *normalized* records with a common shape. Network access is
done with the standard library only, so the tracker has zero pip dependencies.
"""

from __future__ import annotations

import gzip
import http.client
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterator

USER_AGENT = "OPECT-Tracker/1.0 (+https://localhost; literature watcher)"


class FetchError(RuntimeError):
    pass


def http_get(
    url: str,
    *,
    timeout: int = 45,
    retries: int = 3,
    headers: dict[str, str] | None = None,
    polite_delay: float = 0.0,
) -> bytes:
    """GET with retry/backoff, gzip support and a descriptive UA."""
    last: Exception | None = None
    for attempt in range(retries):
        if polite_delay:
            time.sleep(polite_delay)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
                    try:
                        data = gzip.decompress(data)
                    except OSError:
                        pass
                return data
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (400, 401, 403, 404):
                raise FetchError(f"HTTP {exc.code} for {url}") from exc
            if exc.code == 429:
                time.sleep(3 + attempt * 4)
            else:
                time.sleep(1.5 * (attempt + 1))
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.IncompleteRead,
            http.client.HTTPException,
        ) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise FetchError(f"request failed after {retries} attempts: {url} ({last})")


def get_json(url: str, **kwargs) -> dict:
    data = http_get(url, **kwargs)
    try:
        return json.loads(data.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise FetchError(f"invalid JSON from {url}: {exc}") from exc


# --------------------------------------------------------------------------- utils
TAG_RE = re.compile(r"<[^>]+>")


def clean_text(text: str | None) -> str | None:
    if not text:
        return None
    text = TAG_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def norm_title(title: str) -> str:
    t = (title or "").lower()
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()[:400]


def strip_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    return doi.lower() or None


def make_record(
    *,
    title: str,
    doi: str | None = None,
    abstract: str | None = None,
    authors: list[dict] | None = None,
    venue: str | None = None,
    publisher: str | None = None,
    work_type: str | None = None,
    published_date: str | None = None,
    cited_by: int = 0,
    is_open_access: bool = False,
    oa_url: str | None = None,
    landing_url: str | None = None,
    pdf_url: str | None = None,
    keywords: list[str] | None = None,
    source: str,
    source_id: str | None = None,
) -> dict[str, Any] | None:
    title = clean_text(title) or ""
    if len(title) < 8:
        return None
    d = published_date
    if d and not re.match(r"^\d{4}(-\d{2}(-\d{2})?)?", d):
        d = None
    if d and len(d) == 4:
        d = f"{d}-01-01"
    elif d and len(d) == 7:
        d = f"{d}-01"
    year = int(d[:4]) if d else None
    return {
        "title": title,
        "doi": strip_doi(doi),
        "abstract": clean_text(abstract),
        "authors": [a for a in (authors or []) if a.get("name")],
        "venue": clean_text(venue),
        "publisher": clean_text(publisher),
        "work_type": work_type,
        "published_date": d,
        "published_year": year,
        "cited_by": int(cited_by or 0),
        "is_open_access": bool(is_open_access),
        "oa_url": oa_url,
        "landing_url": landing_url,
        "pdf_url": pdf_url,
        "keywords": [k for k in (keywords or []) if k][:24],
        "normalized_title": norm_title(title),
        "sources": [source],
        "source_ids": {source: source_id} if source_id else {},
    }


# ------------------------------------------------------------------------ OpenAlex
OA_BASE = "https://api.openalex.org/works"


def _invert_abstract(inv: dict | None) -> str | None:
    if not inv:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort()
    return " ".join(w for _, w in positions)


def openalex_search(
    query: str,
    *,
    date_from: str | None,
    date_to: str | None,
    per_page: int = 100,
    max_pages: int = 5,
    mailto: str | None = None,
) -> Iterator[dict]:
    select = ",".join(
        [
            "id", "doi", "title", "display_name", "publication_date", "type",
            "cited_by_count", "primary_location", "authorships", "abstract_inverted_index",
            "open_access", "best_oa_location", "keywords", "primary_topic",
        ]
    )
    filters = [f"title_and_abstract.search:{query}"]
    if date_from:
        filters.append(f"from_publication_date:{date_from}")
    if date_to:
        filters.append(f"to_publication_date:{date_to}")
    got_any = False
    for page in range(1, max_pages + 1):
        parts = [
            "filter=" + urllib.parse.quote(",".join(filters), safe=':,"'),
            f"per-page={per_page}",
            f"page={page}",
            "select=" + urllib.parse.quote(select, safe=","),
        ]
        if mailto:
            parts.append("mailto=" + urllib.parse.quote(mailto))
        url = f"{OA_BASE}?{'&'.join(parts)}"
        try:
            data = get_json(url, timeout=60, polite_delay=0.15)
        except FetchError:
            # Keep whatever earlier pages delivered; only a total failure is fatal.
            if got_any:
                break
            raise
        results = data.get("results") or []
        got_any = got_any or bool(results)
        for item in results:
            yield item
        if len(results) < per_page:
            break


def parse_openalex(item: dict) -> dict[str, Any] | None:
    loc = item.get("primary_location") or {}
    src = loc.get("source") or {}
    authors = []
    for auth in item.get("authorships") or []:
        name = (auth.get("author") or {}).get("display_name") or auth.get("raw_author_name")
        if name:
            authors.append({"name": name, "orcid": (auth.get("author") or {}).get("orcid")})
    best = item.get("best_oa_location") or {}
    oa = item.get("open_access") or {}
    keywords = [
        (k.get("display_name") or k.get("keyword") or "")
        for k in (item.get("keywords") or [])
    ]
    topic = item.get("primary_topic") or {}
    if topic.get("display_name"):
        keywords.append(topic["display_name"])
    return make_record(
        title=item.get("display_name") or item.get("title") or "",
        doi=item.get("doi"),
        abstract=_invert_abstract(item.get("abstract_inverted_index")),
        authors=authors,
        venue=src.get("display_name"),
        publisher=src.get("host_organization_name"),
        work_type=item.get("type"),
        published_date=item.get("publication_date"),
        cited_by=item.get("cited_by_count") or 0,
        is_open_access=bool(oa.get("is_oa")),
        oa_url=oa.get("oa_url") or best.get("pdf_url"),
        landing_url=loc.get("landing_page_url"),
        pdf_url=best.get("pdf_url") or loc.get("pdf_url"),
        keywords=keywords,
        source="openalex",
        source_id=(item.get("id") or "").rsplit("/", 1)[-1],
    )


# ------------------------------------------------------------------------ Crossref
CR_BASE = "https://api.crossref.org/works"


def crossref_search(
    query: str,
    *,
    date_from: str | None,
    date_to: str | None,
    rows: int = 100,
    mailto: str | None = None,
) -> Iterator[dict]:
    filters = []
    if date_from:
        filters.append(f"from-pub-date:{date_from}")
    if date_to:
        filters.append(f"until-pub-date:{date_to}")
    params = {
        "query.bibliographic": query,
        "rows": str(rows),
        "sort": "published",
        "order": "desc",
        "select": "DOI,title,abstract,author,container-title,publisher,issued,published,created,type,URL,is-referenced-by-count,subject",
    }
    if filters:
        params["filter"] = ",".join(filters)
    if mailto:
        params["mailto"] = mailto
    url = f"{CR_BASE}?{urllib.parse.urlencode(params)}"
    data = get_json(url, timeout=60, polite_delay=1.0)
    for item in (data.get("message") or {}).get("items") or []:
        yield item


def _cr_date(item: dict) -> str | None:
    for key in ("published", "issued", "published-online", "published-print", "created"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            p = parts[0]
            if len(p) >= 3:
                return f"{p[0]:04d}-{p[1]:02d}-{p[2]:02d}"
            if len(p) == 2:
                return f"{p[0]:04d}-{p[1]:02d}-01"
            return f"{p[0]:04d}-01-01"
    return None


def parse_crossref(item: dict) -> dict[str, Any] | None:
    title = (item.get("title") or [""])[0]
    authors = []
    for a in item.get("author") or []:
        name = " ".join(x for x in [a.get("given"), a.get("family")] if x) or a.get("name")
        if name:
            authors.append({"name": name, "orcid": a.get("ORCID")})
    return make_record(
        title=title,
        doi=item.get("DOI"),
        abstract=item.get("abstract"),
        authors=authors,
        venue=(item.get("container-title") or [None])[0],
        publisher=item.get("publisher"),
        work_type=item.get("type"),
        published_date=_cr_date(item),
        cited_by=item.get("is-referenced-by-count") or 0,
        landing_url=item.get("URL"),
        keywords=item.get("subject") or [],
        source="crossref",
        source_id=item.get("DOI"),
    )


# -------------------------------------------------------------------------- PubMed
EU_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def pubmed_search(
    term: str,
    *,
    date_from: str | None,
    date_to: str | None,
    retmax: int = 100,
    mailto: str | None = None,
) -> Iterator[dict]:
    if date_from or date_to:
        start = (date_from or "2015-01-01").replace("-", "/")
        end = (date_to or date.today().isoformat()).replace("-", "/")
        term = f"({term}) AND {start}:{end}[dp]"
    params = {"db": "pubmed", "term": term, "retmode": "json", "retmax": str(retmax), "sort": "date"}
    if mailto:
        params["email"] = mailto
    search = get_json(f"{EU_BASE}/esearch.fcgi?{urllib.parse.urlencode(params)}", timeout=45, polite_delay=0.4)
    ids = (search.get("esearchresult") or {}).get("idlist") or []
    for chunk_start in range(0, len(ids), 100):
        chunk = ids[chunk_start : chunk_start + 100]
        params = {"db": "pubmed", "id": ",".join(chunk), "retmode": "xml"}
        if mailto:
            params["email"] = mailto
        raw = http_get(f"{EU_BASE}/efetch.fcgi?{urllib.parse.urlencode(params)}", timeout=60, polite_delay=0.4)
        yield from _parse_pubmed_xml(raw)


def _parse_pubmed_xml(raw: bytes) -> Iterator[dict]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return
    for art in root.findall(".//PubmedArticle"):
        try:
            title = "".join(art.findtext(".//ArticleTitle") or "").strip()
            abstract_parts = []
            for ab in art.findall(".//Abstract/AbstractText"):
                label = ab.get("Label")
                text = "".join(ab.itertext()).strip()
                if text:
                    abstract_parts.append(f"{label}: {text}" if label else text)
            abstract = " ".join(abstract_parts)
            journal = art.findtext(".//Journal/Title")
            date_str = None
            pd = art.find(".//Journal/JournalIssue/PubDate")
            if pd is not None:
                year = pd.findtext("Year")
                month = pd.findtext("Month")
                day = pd.findtext("Day")
                if year:
                    month_num = _month_num(month)
                    date_str = f"{year}-{month_num:02d}-{int(day):02d}" if day and day.isdigit() else (
                        f"{year}-{month_num:02d}-01" if month_num else f"{year}-01-01"
                    )
            if not date_str:
                date_str = art.findtext(".//PubMedPubDate[@PubStatus='pubmed']/Year")
                date_str = f"{date_str}-01-01" if date_str else None
            authors = []
            for a in art.findall(".//AuthorList/Author"):
                name = " ".join(
                    x for x in [a.findtext("ForeName"), a.findtext("LastName")] if x
                ) or a.findtext("CollectiveName")
                if name:
                    authors.append({"name": name})
            doi = None
            for aid in art.findall(".//ArticleIdList/ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = (aid.text or "").strip()
            pmid = art.findtext(".//PMID")
            keywords = [k.text for k in art.findall(".//KeywordList/Keyword") if k.text]
            pubtypes = [p.text for p in art.findall(".//PublicationType") if p.text]
            rec = make_record(
                title=title,
                doi=doi,
                abstract=abstract,
                authors=authors,
                venue=journal,
                work_type="article",
                published_date=date_str,
                keywords=keywords,
                source="pubmed",
                source_id=pmid,
            )
            if rec:
                rec["pubmed_types"] = pubtypes
                yield rec
        except Exception:  # noqa: BLE001 - one bad record should not kill the sync
            continue


def _month_num(month: str | None) -> int:
    if not month:
        return 0
    month = month.strip()
    if month.isdigit():
        return max(1, min(12, int(month)))
    for i, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    ):
        if month.lower().startswith(name):
            return i
    return 0


# --------------------------------------------------------------------------- arXiv
ARXIV_BASE = "http://export.arxiv.org/api/query"
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def arxiv_search(query: str, *, max_results: int = 100) -> Iterator[dict]:
    params = {
        "search_query": query,
        "start": "0",
        "max_results": str(max_results),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    raw = http_get(f"{ARXIV_BASE}?{urllib.parse.urlencode(params)}", timeout=60, polite_delay=3.0)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return
    for entry in root.findall("a:entry", ATOM):
        title = " ".join((entry.findtext("a:title", default="", namespaces=ATOM)).split())
        summary = " ".join((entry.findtext("a:summary", default="", namespaces=ATOM)).split())
        published = entry.findtext("a:published", default="", namespaces=ATOM)
        authors = [
            {"name": a.findtext("a:name", default="", namespaces=ATOM)}
            for a in entry.findall("a:author", ATOM)
        ]
        link_pdf = None
        for link in entry.findall("a:link", ATOM):
            if link.get("title") == "pdf":
                link_pdf = link.get("href")
        doi = entry.findtext("arxiv:doi", default=None, namespaces=ATOM)
        categories = [c.get("term") for c in entry.findall("a:category", ATOM) if c.get("term")]
        rec = make_record(
            title=title,
            doi=doi,
            abstract=summary,
            authors=authors,
            venue="arXiv",
            work_type="preprint",
            published_date=published[:10] if published else None,
            landing_url=entry.findtext("a:id", default=None, namespaces=ATOM),
            pdf_url=link_pdf,
            keywords=categories,
            source="arxiv",
            source_id=entry.findtext("a:id", default="", namespaces=ATOM).rsplit("/", 1)[-1],
        )
        if rec:
            yield rec
