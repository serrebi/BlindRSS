"""
Full-text article extraction using trafilatura.

Goal:
- Given an article URL, extract clean text (no ads/boilerplate) plus title and author.
- Follow simple multi-page pagination (rel=next / next links) and merge text.
- Provide safe fallbacks for feed items without a webpage URL (e.g., podcast episodes).
"""

from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, List, Set
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from core import utils

LOG = logging.getLogger(__name__)

try:
    import trafilatura
    from trafilatura.metadata import extract_metadata
except Exception:
    trafilatura = None
    extract_metadata = None


class ExtractionError(RuntimeError):
    """Raised when an extraction attempt fails in a way worth surfacing to the UI."""
    pass


@dataclass
class FullArticle:
    url: str
    title: str
    author: str
    text: str


_MEDIA_EXTS = (
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus",
    ".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    ".pdf",
)

_LEAD_RECOVERY_ALLOWED_NETLOC_SUFFIXES = {
    # Some sites have a meaningful lead/intro in the HTML meta description that trafilatura may
    # skip when running in precision mode.
    "wirtualnemedia.pl",
}

_LEAD_RECOVERY_MIN_PRECISION_LEN = 200
_LEAD_RECOVERY_MIN_DESC_LEN = 60
_LEAD_RECOVERY_DESC_SNIPPET_LEN = 120
_LEAD_RECOVERY_DESC_HIT_SNIPPET_LEN = 80
_LEAD_RECOVERY_MAX_RECALL_NORM_CHARS = 8000
_LEAD_RECOVERY_MAX_SCAN_PARAS = 8
_LEAD_RECOVERY_MIN_PARA_LEN = 40
_LEAD_RECOVERY_MAX_PARA_LEN = 800
_LEAD_RECOVERY_MIN_PUNCT_PARA_LEN = 120
_LEAD_RECOVERY_MAX_INTRO_PARAS = 2

_TITLE_SUFFIX_STRIP_SEPARATORS = (" | ", " — ", " – ")
_META_DESCRIPTION_TAG_ATTRS: List[dict] = [
    {"property": "og:description"},
    {"name": "description"},
    {"name": "twitter:description"},
]
_META_TITLE_TAG_ATTRS: List[dict] = [
    {"property": "og:title"},
    {"name": "twitter:title"},
    {"name": "title"},
]


def _lead_recovery_enabled(url: str) -> bool:
    if not url:
        return False
    try:
        host = urlsplit(url).hostname
    except (TypeError, ValueError):
        return False
    if not host:
        return False
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in _LEAD_RECOVERY_ALLOWED_NETLOC_SUFFIXES)


def _looks_like_media_url(url: str) -> bool:
    try:
        path = (urlsplit(url).path or "").lower()
        return any(path.endswith(ext) for ext in _MEDIA_EXTS)
    except Exception:
        return False


def _normalize_whitespace(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_paragraphs(text: str) -> List[str]:
    t = _normalize_whitespace(text or "")
    if not t:
        return []
    if "\n\n" in t:
        return [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    # Trafilatura often emits single-newline-separated paragraphs (no blank lines).
    return [p.strip() for p in t.split("\n") if p.strip()]


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _strip_trailing_ellipsis(text: str) -> str:
    return re.sub(r"(?:\.\.\.|…)\s*$", "", (text or "").strip()).strip()


def _strip_title_suffix(title: str) -> str:
    t = (title or "").strip()
    for sep in _TITLE_SUFFIX_STRIP_SEPARATORS:
        if sep in t:
            # Split from the right and take the longest segment.
            # This tends to drop short site-name suffix/prefix; we intentionally avoid stripping " - "
            # because it's common in legitimate titles.
            return max(t.rsplit(sep, 1), key=len).strip()
    return t


def _extract_meta_content(soup: BeautifulSoup, candidates: List[dict]) -> str:
    for attrs in candidates:
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            content = (tag.get("content") or "").strip()
            if content:
                return content
    return ""


def _parse_html_soup(html: Optional[str], *, context: str) -> Optional[BeautifulSoup]:
    if not html:
        return None
    try:
        return BeautifulSoup(html, "html.parser")
    except Exception:
        LOG.debug("Failed to parse HTML for %s", context, exc_info=True)
        return None


def _extract_meta_description(*, html: Optional[str] = None, soup: Optional[BeautifulSoup] = None) -> str:
    if soup is None:
        soup = _parse_html_soup(html, context="meta description")
        if soup is None:
            return ""

    try:
        return _extract_meta_content(
            soup,
            _META_DESCRIPTION_TAG_ATTRS,
        )
    except Exception:
        LOG.debug("Failed to extract meta description", exc_info=True)
        return ""


def _extract_page_title(*, html: Optional[str] = None, soup: Optional[BeautifulSoup] = None) -> str:
    if soup is None:
        soup = _parse_html_soup(html, context="page title")
        if soup is None:
            return ""

    try:
        meta_title = _extract_meta_content(
            soup,
            _META_TITLE_TAG_ATTRS,
        )
        if meta_title:
            return meta_title
        t = soup.find("title")
        if t and t.get_text(strip=True):
            return t.get_text(strip=True)
    except Exception:
        LOG.debug("Failed to extract page title", exc_info=True)
        return ""
    return ""


def _recover_intro_paragraphs(
    recall_text: str,
    *,
    precision_norm: str,
    page_title_norm: str,
    desc_hit_snippet: str,
) -> List[str]:
    intro: List[str] = []
    desc_hit = False

    for p in _split_paragraphs(recall_text)[:_LEAD_RECOVERY_MAX_SCAN_PARAS]:
        pn = _normalize_for_match(p)
        if not pn:
            continue
        if pn in precision_norm:
            break
        if page_title_norm and pn == page_title_norm:
            continue
        if len(p) < _LEAD_RECOVERY_MIN_PARA_LEN or len(p) > _LEAD_RECOVERY_MAX_PARA_LEN:
            continue
        if not re.search(r"[.!?]", p) and len(p) < _LEAD_RECOVERY_MIN_PUNCT_PARA_LEN:
            continue
        if desc_hit_snippet and desc_hit_snippet in pn:
            desc_hit = True
        intro.append(p)
        if len(intro) >= _LEAD_RECOVERY_MAX_INTRO_PARAS:
            break

    if not intro or not desc_hit:
        return []
    return intro


def _attempt_lead_recovery(
    html: str,
    url: str,
    *,
    precision_text: str,
    precision_norm: str,
    do_extract: Callable[[dict], str],
) -> Optional[str]:
    if not _lead_recovery_enabled(url):
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        LOG.debug("Failed to parse HTML for lead recovery", exc_info=True)
        return None

    desc = _strip_trailing_ellipsis(_extract_meta_description(soup=soup))
    desc_norm = _normalize_for_match(desc)
    if not desc_norm or len(desc_norm) < _LEAD_RECOVERY_MIN_DESC_LEN:
        return None

    desc_snippet = desc_norm[:_LEAD_RECOVERY_DESC_SNIPPET_LEN]
    if desc_snippet in precision_norm:
        return None

    txt_rec = do_extract({"favor_recall": True})
    rec = (txt_rec or "").strip()
    if not rec:
        return None

    rec_head_norm = _normalize_for_match(rec[:_LEAD_RECOVERY_MAX_RECALL_NORM_CHARS])
    if desc_snippet not in rec_head_norm:
        return None

    page_title = _strip_title_suffix(_extract_page_title(soup=soup))
    page_title_norm = _normalize_for_match(page_title)

    intro = _recover_intro_paragraphs(
        rec,
        precision_norm=precision_norm,
        page_title_norm=page_title_norm,
        desc_hit_snippet=desc_snippet[:_LEAD_RECOVERY_DESC_HIT_SNIPPET_LEN],
    )
    if not intro:
        return None

    combined = "\n\n".join(intro + [precision_text])
    return (combined or "").strip()


_ZDNET_BOILERPLATE_PATTERNS: List[re.Pattern] = [
    re.compile(r"^\s*ZDNET\s+Recommends\b", re.I),
    re.compile(r"^\s*What\s+exactly\s+does\s+it\s+mean\?\s*$", re.I),
    re.compile(r"\bZDNET's\s+recommendations\s+are\s+based\s+on\b", re.I),
    re.compile(r"\bhours\s+of\s+testing\b", re.I),
    re.compile(r"\bcomparison\s+shopping\b", re.I),
    re.compile(r"\bvendor\s+and\s+retailer\s+listings\b", re.I),
    re.compile(r"\baffiliate\s+commissions\b", re.I),
    re.compile(r"\bdoes\s+not\s+affect\s+the\s+price\s+you\s+pay\b", re.I),
    re.compile(r"\bstrict\s+guidelines\b", re.I),
    re.compile(r"\beditorial\s+content\b.*\badvertisers\b", re.I),
    re.compile(r"\bOur\s+goal\s+is\s+to\s+deliver\b", re.I),
    re.compile(r"\bfact-?check\b", re.I),
    re.compile(r"\breport\s+the\s+mistake\b", re.I),
    re.compile(r"^\s*Follow\s+ZDNET\b", re.I),
    re.compile(r"\bAdd\s+us\s+as\s+a\s+preferred\s+source\s+on\s+Google\b", re.I),
    re.compile(r"\bpreferred\s+source\s+on\s+Google\b", re.I),
    re.compile(r"\bFollow\s+ZDNET\b", re.I),
]


def _strip_zdnet_recommends_block(text: str) -> str:
    """Backward-compatible name: strip common ZDNET boilerplate paragraphs near the top.

    ZDNET sometimes injects disclosure / recommendation / follow blocks at the start of the extracted text.
    We only remove paragraphs that match known patterns, and only within the first N paragraphs to avoid
    deleting real content.
    """
    paras = _split_paragraphs(text)
    if not paras:
        return ""

    max_scan = min(25, len(paras))
    i = 0
    while i < max_scan:
        p = (paras[i] or "").strip()
        if not p:
            i += 1
            continue

        if any(rx.search(p) for rx in _ZDNET_BOILERPLATE_PATTERNS):
            i += 1
            continue

        # A few pages split disclosure headings into tiny chunks.
        if i < 10 and re.search(r"\bZDNET\b", p, re.I) and (
            re.search(r"\brecommend", p, re.I)
            or re.search(r"\bpreferred\s+source\b", p, re.I)
            or re.search(r"\bfollow\b", p, re.I)
        ):
            i += 1
            continue

        break

    cleaned = "\n\n".join(paras[i:]).strip()
    return cleaned


def _postprocess_extracted_text(text: str, url: str) -> str:
    t = _normalize_whitespace(text or "")
    if not t:
        return ""

    netloc = ""
    try:
        netloc = (urlsplit(url or "").netloc or "").lower()
    except Exception:
        netloc = ""

    if netloc.endswith("zdnet.com"):
        t = _strip_zdnet_recommends_block(t)

    return _normalize_whitespace(t)


def _download_html(url: str, timeout: int = 20) -> Optional[str]:
    """Download a URL and return HTML as text."""
    if not url:
        return None

    try:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        r = utils.safe_requests_get(url, timeout=timeout, headers=headers, allow_redirects=True)
        if 200 <= r.status_code < 400:
            r.encoding = r.encoding or "utf-8"
            return r.text
        return None
    except Exception:
        return None


def _extract_title_author_from_meta(html: str, url: str) -> Tuple[str, str]:
    title = ""
    author = ""

    if trafilatura is not None and extract_metadata is not None and html:
        try:
            meta = extract_metadata(html, url=url)
            if meta:
                title = (meta.title or "") if hasattr(meta, "title") else ""
                author = (meta.author or "") if hasattr(meta, "author") else ""
        except Exception:
            pass

    if not title:
        try:
            soup = BeautifulSoup(html, "html.parser")
            t = soup.find("title")
            if t and t.get_text(strip=True):
                title = t.get_text(strip=True)
        except Exception:
            pass

    return (title or "").strip(), (author or "").strip()


def _trafilatura_extract_text(html: str, url: str = "") -> str:
    """Try to get the main article text using trafilatura.

    CPU considerations:
    - Prefer precision-first extraction to reduce boilerplate.
    - Only fall back to recall mode when the precision result is clearly too short.
    - For some sites, precision extraction may skip a lead/intro; in that case, try recall and
      prepend the missing intro paragraphs to the precision result.
    """
    if not html or trafilatura is None:
        return ""

    base_kwargs = dict(
        output_format="txt",
        include_comments=False,
        include_images=False,
        include_links=False,
        include_tables=False,
        deduplicate=True,
    )

    def _do_extract(extra_kwargs):
        try:
            return trafilatura.extract(
                html,
                url=url or None,
                **base_kwargs,
                **extra_kwargs,
            )
        except TypeError:
            # Older/newer trafilatura versions may not support all kwargs.
            safe_kwargs = dict(base_kwargs)
            safe_kwargs.update(extra_kwargs)
            for k in list(safe_kwargs.keys()):
                if k not in ("output_format", "include_comments", "include_images", "include_links", "include_tables", "deduplicate", "favor_recall", "favor_precision"):
                    safe_kwargs.pop(k, None)
            return trafilatura.extract(html, url=url or None, **safe_kwargs)
        except Exception:
            return ""

    # Precision-first
    txt_prec = _do_extract({"favor_precision": True, "favor_recall": False})
    prec = (txt_prec or "").strip()
    if prec and len(prec) >= _LEAD_RECOVERY_MIN_PRECISION_LEN:
        prec_norm = _normalize_for_match(prec)
        recovered = _attempt_lead_recovery(
            html,
            url,
            precision_text=prec,
            precision_norm=prec_norm,
            do_extract=_do_extract,
        )
        if recovered:
            return recovered

        return prec

    # Recall fallback (only when precision is empty/too short)
    txt_rec = _do_extract({"favor_recall": True})
    return (txt_rec or "").strip()


def _soup_extract_text(html: str) -> str:
    """Fallback: crude visible text extraction using BeautifulSoup."""
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, "html.parser")
        # remove obvious junk
        for tag in soup(["script", "style", "noscript", "svg", "canvas", "iframe"]):
            tag.decompose()
        # prefer main-ish containers
        main = soup.find("article") or soup.find("main")
        node = main if main else soup.body if soup.body else soup
        text = node.get_text("\n", strip=True)
        return (text or "").strip()
    except Exception:
        return ""


def _extract_text_any(html: str, url: str = "") -> str:
    txt = _trafilatura_extract_text(html, url=url)
    if txt:
        return _normalize_whitespace(txt)
    txt = _soup_extract_text(html)
    return _normalize_whitespace(txt)


def _find_next_page(html: str, base_url: str) -> Optional[str]:
    """Return absolute next-page URL if present, else None."""
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")

        # 1) <link rel="next" href="...">
        link = soup.find("link", attrs={"rel": lambda v: v and "next" in (v if isinstance(v, list) else [v])})
        if link and link.get("href"):
            href = link.get("href").strip()
            if href:
                return urljoin(base_url, href)

        # 2) <a rel="next" href="...">
        a = soup.find("a", attrs={"rel": lambda v: v and "next" in (v if isinstance(v, list) else [v])})
        if a and a.get("href"):
            href = a.get("href").strip()
            if href:
                return urljoin(base_url, href)

        # 3) common "next" anchors/buttons
        for tag in soup.find_all("a", href=True):
            href = (tag.get("href") or "").strip()
            if not href:
                continue
            text = (tag.get_text(" ", strip=True) or "").lower()
            cls = " ".join(tag.get("class") or []).lower()
            aria = (tag.get("aria-label") or "").lower()
            if (
                any(k in text for k in ("next", "older", "next page"))
                or text in (">", ">>", "›", "»")
                or "next" in cls
                or aria.startswith("next")
            ):
                absu = urljoin(base_url, href)
                # avoid obvious comment/share links
                if any(x in absu.lower() for x in ("facebook.com", "twitter.com", "x.com", "linkedin.com", "pinterest.com")):
                    continue
                return absu
    except Exception:
        return None

    return None


def _merge_texts(texts: List[str]) -> str:
    """Merge multiple page texts while de-duplicating repeated blocks."""
    seen: Set[str] = set()
    out: List[str] = []

    for t in texts:
        t = (t or "").strip()
        if not t:
            continue

        # de-dupe paragraph by paragraph
        paras = [p.strip() for p in t.split("\n") if p.strip()]
        merged_paras: List[str] = []
        for p in paras:
            key = re.sub(r"\s+", " ", p).strip().lower()
            if len(key) < 25:
                continue
            if key in seen:
                continue
            seen.add(key)
            merged_paras.append(p)

        if merged_paras:
            out.append("\n".join(merged_paras))

    return _normalize_whitespace("\n\n".join(out))


def extract_full_article(url: str, max_pages: int = 6, timeout: int = 20) -> Optional[FullArticle]:
    """
    Extract full article text from a URL. Attempts to follow pagination for multi-page articles.

    Returns FullArticle or None on unsupported/empty.
    Raises ExtractionError for download/extraction failures that should be shown to the user.
    """
    url = (url or "").strip()
    if not url or _looks_like_media_url(url):
        return None
    if trafilatura is None:
        raise ExtractionError("trafilatura is not installed or failed to import. Reinstall requirements.")

    visited: Set[str] = set()
    page_texts: List[str] = []

    current = url
    title = ""
    author = ""

    downloaded_any = False

    for _ in range(max_pages):
        if not current or current in visited:
            break
        visited.add(current)

        html = _download_html(current, timeout=timeout)
        if not html:
            break
        downloaded_any = True

        if not title or not author:
            t, a = _extract_title_author_from_meta(html, current)
            if not title:
                title = t
            if not author:
                author = a

        page_texts.append(_extract_text_any(html, current))

        next_url = _find_next_page(html, current)
        if not next_url or next_url in visited:
            break
        current = next_url
        time.sleep(0.15)

    if not downloaded_any:
        raise ExtractionError("Download failed (site blocked, offline, or connection problem).")

    merged = _merge_texts(page_texts)
    merged = _postprocess_extracted_text(merged, url)
    if not merged:
        raise ExtractionError("Downloaded page, but could not extract readable text (empty result).")

    return FullArticle(url=url, title=title or "", author=author or "", text=merged)


def extract_from_html(html: str, source_url: str = "", title: str = "", author: str = "") -> Optional[FullArticle]:
    """
    Extract readable text from HTML already available in the feed item (fallback when no webpage URL exists).
    """
    html = (html or "").strip()
    if not html:
        return None
    text = _extract_text_any(html, source_url or "")
    text = _postprocess_extracted_text(text, source_url or "")
    if not text:
        return None

    # Prefer metadata extracted from HTML if present.
    t2, a2 = _extract_title_author_from_meta(html, source_url or "")
    final_title = (title or t2 or "").strip()
    final_author = (author or a2 or "").strip()

    return FullArticle(url=source_url or "", title=final_title, author=final_author, text=text)


def render_full_article(
    url: str,
    *,
    fallback_html: str = "",
    fallback_title: str = "",
    fallback_author: str = "",
    max_pages: int = 6,
    timeout: int = 20,
) -> Optional[str]:
    """
    Render a full article into a single plain-text string (Title/Author/Text).

    Behavior:
    - If url is missing or looks like media, try fallback_html (feed content) and return that.
    - If url extraction fails, try fallback_html; if still fails, raise ExtractionError.
    """
    url = (url or "").strip()

    def _render(art: FullArticle) -> str:
        parts: List[str] = []
        parts.append(f"Title: {art.title.strip() or '(unknown)'}")
        parts.append(f"Author: {art.author.strip() or '(unknown)'}")
        parts.append("")
        body = _postprocess_extracted_text(art.text or "", url)
        parts.append(body.strip())
        return (_normalize_whitespace("\n".join(parts)) + "\n")

    # No usable URL: fall back to feed content.
    if not url or _looks_like_media_url(url):
        art = extract_from_html(fallback_html, "", title=fallback_title, author=fallback_author)
        if art:
            return _render(art)
        return None

    # Try webpage extraction.
    try:
        art = extract_full_article(url, max_pages=max_pages, timeout=timeout)
        if art:
            return _render(art)
    except ExtractionError:
        # will be handled below with fallback
        raise
    except Exception as e:
        raise ExtractionError(str(e) or "Unknown extraction error")

    # If URL extraction returned None, try fallback content.
    art = extract_from_html(fallback_html, url, title=fallback_title, author=fallback_author)
    if art:
        return _render(art)

    raise ExtractionError("Could not extract full text from the webpage or from feed content.")
