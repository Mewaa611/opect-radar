"""Relevance scoring and topic tagging for OPECT literature.

The scorer is intentionally transparent: every paper keeps the keyword hits
that produced its score, so the UI can explain *why* something was included.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

WORD_RE = re.compile(r"[a-z0-9]+")
CJK_RE = re.compile(r"[\u3400-\u9fff]")

# Terms that make a photo + transistor co-occurrence uninteresting for this tracker.
_OECT_ONLY = re.compile(
    r"(organic electrochemical transistor|oect|pedot:pss|mixed ionic)", re.I
)
_PHOTO = re.compile(
    r"(photo|light[- ]?(induced|responsive|gated|driven|modulat|stimul)|illuminat|irradiat|"
    r"photocurrent|photovoltage|photoelectr|optical)", re.I
)
# Inorganic-only device papers (IGZO, MoS2, GaN ...) are a common false positive:
# they are optoelectronic transistors, but not *organic* photoelectrochemical ones.
_INORGANIC = re.compile(
    r"(igzo|indium gallium zinc|zno|gan|sio2|hfo2|al2o3|mos2|mose2|ws2|wse2|"
    r"black phosphorus|in2o3|ito\b|perovskite transistor|carbon nanotube transistor|"
    r"graphene transistor|silicon transistor|si transistor)", re.I
)
_ORGANIC = re.compile(
    r"(organic|polymer|pedot|conjugated|omiec|small molecule|molecular|biocompatib)", re.I
)
_PEC = re.compile(r"(photoelectrochem|photoelectrode|photogate|photoactive gate|photocurrent)", re.I)


def normalize(text: str | None) -> str:
    """Lowercase, strip accents, collapse punctuation to single spaces."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
    text = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", text)
    text = re.sub(r"<[^>]+>", " ", text)  # strip JATS/HTML tags from abstracts
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _contains(haystack: str, phrase: str) -> bool:
    if not phrase:
        return False
    if CJK_RE.search(phrase):
        return phrase in haystack
    # Word-boundary-ish match: phrase surrounded by non-alphanumerics.
    pattern = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


@dataclass
class Hit:
    label: str
    weight: int
    title: bool
    abstract: bool
    keywords: bool


@dataclass
class Score:
    score: float = 0.0
    hits: list[Hit] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    reason: str = ""
    tier: str = "excluded"

    @property
    def matched_labels(self) -> list[str]:
        return [h.label for h in self.hits]


class RelevanceScorer:
    def __init__(self, config: dict):
        rel = config.get("relevance", {})
        self.include_threshold = float(rel.get("include_threshold", 30))
        self.core_threshold = float(rel.get("core_threshold", 70))
        self.store_threshold = float(rel.get("store_threshold", 18))
        weights = rel.get("weights", {})
        self.w_title = float(weights.get("title", 1.0))
        self.w_abstract = float(weights.get("abstract", 0.6))
        self.w_keywords = float(weights.get("keywords", 0.8))
        self.w_venue = float(weights.get("venue", 0.35))
        self.groups = [
            g
            for g in config.get("keywords", [])
            if g.get("phrases") or g.get("tokens")
        ]
        self.negatives = [normalize(p) for p in config.get("negative_keywords", [])]
        self.topics = config.get("topics", [])

    # ------------------------------------------------------------------ score
    def score(
        self,
        title: str | None,
        abstract: str | None,
        keywords: list[str] | None = None,
        venue: str | None = None,
    ) -> Score:
        t = normalize(title)
        a = normalize(abstract)
        kw = normalize(" ; ".join(keywords or []))
        v = normalize(venue)
        haystack = " ".join([t, a, kw, v])

        hits: list[Hit] = []
        raw = 0.0
        coverage = 0

        for group in self.groups:
            in_title = any(_contains(t, normalize(p)) for p in group.get("phrases", []))
            in_abs = any(_contains(a, normalize(p)) for p in group.get("phrases", []))
            in_kw = any(_contains(kw, normalize(p)) for p in group.get("phrases", []))
            in_venue = any(_contains(v, normalize(p)) for p in group.get("phrases", []))

            if not (in_title or in_abs or in_kw or in_venue):
                continue

            weight = float(group.get("weight", 0))
            multiplier = group.get("title", 1.0)
            contribution = 0.0
            if in_title:
                contribution += weight * self.w_title * multiplier
            if in_abs:
                # A concept that only appears in the abstract is weaker evidence
                # than the same concept in the title.
                contribution += weight * self.w_abstract * 0.72
            if in_kw:
                contribution += weight * self.w_keywords
            if in_venue and not (in_title or in_abs):
                contribution += weight * self.w_venue

            raw += contribution
            coverage += 1
            hits.append(Hit(group["label"], int(weight), in_title, in_abs, in_kw))

        # Coverage bonus: independent lines of evidence matter more than repeats.
        if coverage >= 2:
            raw *= 1.0 + min(0.45, 0.12 * (coverage - 1))

        # Cross-domain requirement: photo + transistor/OECT must both be present.
        photo = bool(_PHOTO.search(haystack))
        oect = bool(_OECT_ONLY.search(haystack))
        transistor = "transistor" in haystack or "oect" in haystack or "晶体管" in haystack
        if not (photo and (oect or transistor)):
            raw *= 0.45

        # Core concept gets a bonus and a floor, so explicit OPECT papers always
        # rank above merely adjacent work.
        core_hit = any(h.label == "OPECT" for h in hits)
        if core_hit:
            raw += 30

        # Organic requirement: a paper about an inorganic phototransistor with no
        # organic/polymer/photoelectrochemical context is only marginally relevant.
        if _INORGANIC.search(t) and not _ORGANIC.search(haystack) and not _PEC.search(haystack):
            raw *= 0.3
        elif _INORGANIC.search(t) and not _ORGANIC.search(haystack) and not core_hit:
            raw *= 0.6

        for neg in self.negatives:
            if neg and _contains(haystack, neg):
                raw *= 0.35
                break

        # Compress the long tail; the divisor keeps core papers spread over
        # ~70-97 instead of all saturating at 99.9.
        score = 100.0 * (1.0 - math.exp(-max(raw, 0.0) / 175.0))
        if core_hit:
            score = max(score, 62.0)

        topics = self.tag_topics(t, a, kw, v)
        if core_hit:
            tier = "core"
        elif score >= self.include_threshold:
            tier = "related"
        elif score >= self.store_threshold:
            tier = "peripheral"
        else:
            tier = "excluded"

        return Score(
            score=round(score, 1),
            hits=hits,
            topics=topics,
            reason=self._reason(hits, core_hit, photo, oect),
            tier=tier,
        )

    # ------------------------------------------------------------------ topics
    def tag_topics(self, title: str, abstract: str, keywords: str, venue: str) -> list[str]:
        text = " ".join([title, abstract, keywords, venue])
        out: list[str] = []
        for topic in self.topics:
            terms = topic.get("any", [])
            if any(_contains(text, normalize(term)) for term in terms):
                out.append(topic["label"])
        return out[:6]

    # ------------------------------------------------------------------ helper
    @staticmethod
    def _reason(hits: list[Hit], core: bool, photo: bool, oect: bool) -> str:
        labels = [h.label for h in hits]
        if core:
            return "明确涉及 OPECT / 光电化学晶体管"
        if photo and oect:
            return "光响应与 OECT/晶体管交叉"
        if photo and labels:
            return "光电化学 + " + "、".join(labels[:2])
        if labels:
            return "命中 " + "、".join(labels[:3])
        return "弱相关"

    def is_relevant(self, score: Score) -> bool:
        return score.score >= self.include_threshold or score.tier == "core"
