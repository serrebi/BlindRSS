import requests
import re
import uuid
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
import email.utils
from dateutil import parser as dateparser
from io import BytesIO
from core.db import get_connection

log = logging.getLogger(__name__)

# Default headers for network calls
HEADERS = {
    "User-Agent": "BlindRSS/1.0 (+https://github.com/)",
    "Accept": "*/*",
}

def safe_requests_get(url, **kwargs):
    """requests.get with default headers and sane timeouts."""
    headers = kwargs.pop("headers", None) or {}
    merged = HEADERS.copy()
    merged.update(headers)
    if "timeout" not in kwargs:
        kwargs["timeout"] = 15
    return requests.get(url, headers=merged, **kwargs)

# --- Date Parsing ---

def format_datetime(dt: datetime) -> str:
    """Return UTC-normalized string for consistent ordering."""
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def extract_date_from_text(text: str):
    """
    Try multiple date patterns inside arbitrary text.
    Returns datetime or None.
    """
    if not text:
        return None
    # 1) numeric with / or -
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", text)
    if m:
        a, b, year = m.groups()
        try:
            year_int = int(year)
            if year_int < 100:
                year_int += 2000 if year_int < 70 else 1900
            a_int, b_int = int(a), int(b)
            # heuristic: if both <=12, prefer US mm/dd
            if a_int > 12 and b_int <= 12:
                day, month = a_int, b_int
            elif b_int > 12 and a_int <= 12:
                day, month = b_int, a_int
            else:
                month, day = a_int, b_int
            return datetime(year_int, month, day)
        except Exception:
            pass
    # 2) URL-style /yyyy/mm/dd/ (common in blogs)
    m_url = re.search(r"/(20\d{2}|19\d{2})/(0?[1-9]|1[0-2])/(0?[1-9]|[12][0-9]|3[01])/", text)
    if m_url:
        try:
            y, mth, d = m_url.groups()
            return datetime(int(y), int(mth), int(d))
        except Exception:
            pass

    # 3) ISO-like yyyy-mm-dd
    m_iso = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m_iso:
        try:
            y, mth, d = map(int, m_iso.groups())
            return datetime(y, mth, d)
        except Exception:
            pass
    # 4) Month name forms (e.g., May 17 2021)
    # Clean up timezone abbreviations that confuse parser
    clean_text = re.sub(r'\b(PST|PDT|EST|EDT|CST|CDT|MST|MDT|AI|GMT|UTC)\b', '', text, flags=re.IGNORECASE)
    # Insert a space before month names if glued to previous letters (e.g., "ASHWINNov 17, 2025")
    clean_text = re.sub(r'([A-Za-z])(?=(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b)', r'\1 ', clean_text)
    # Only attempt parsing if we see something that looks like a date (Month name)
    # Simple heuristic: Check for month names
    if re.search(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', clean_text, re.IGNORECASE):
        try:
            dt = dateparser.parse(clean_text, fuzzy=True, default=datetime(1900, 1, 1))
            if dt.year > 1900:
                return dt
        except Exception:
            pass
    return None

def normalize_date(raw_date_input: any, title: str = "", content: str = "", url: str = "") -> str:
    """
    Robust date normalizer.
    Prioritizes dates found explicitly in the Title or URL, as some feeds (e.g. archives) 
    put the original air date there while using a recent timestamp for pubDate.
    
    raw_date_input can be a string, a datetime object, or a time.struct_time.
    """
    now = datetime.now(timezone.utc)
    
    def valid(dt: datetime) -> bool:
        if not dt: return False
        if dt.tzinfo:
            dt_cmp = dt.astimezone(timezone.utc)
        else:
            # Assume UTC if naive, unless logic dictates otherwise. 
            dt_cmp = dt.replace(tzinfo=timezone.utc)
        
        # Allow only minimal future skew (scheduled posts). Clamp to 2 days.
        return (dt_cmp - now) <= timedelta(days=2) and dt_cmp.year >= 1900

    def parse_unix_timestamp(val):
        """Handle Unix timestamps (seconds or milliseconds)."""
        try:
            if isinstance(val, (int, float)):
                ts = float(val)
            elif isinstance(val, str):
                stripped = val.strip()
                if not re.fullmatch(r"-?\d+(?:\.\d+)?", stripped):
                    return None
                ts = float(stripped)
            else:
                return None

            # Heuristic: values above 1e12 are likely milliseconds
            if abs(ts) > 1e12:
                ts = ts / 1000.0

            dt = datetime.fromtimestamp(ts, timezone.utc)
            return dt if valid(dt) else None
        except Exception:
            return None

    # 0) Handle pre-parsed objects
    if isinstance(raw_date_input, datetime):
        if valid(raw_date_input): return format_datetime(raw_date_input)
    elif hasattr(raw_date_input, 'tm_year'): # struct_time
        try:
            dt = datetime(*raw_date_input[:6], tzinfo=timezone.utc)
            if valid(dt): return format_datetime(dt)
        except: pass

    # 0b) Unix timestamp strings/ints (used by some APIs like Inoreader/BazQux)
    ts_dt = parse_unix_timestamp(raw_date_input)
    if ts_dt:
        return format_datetime(ts_dt)

    # 1) Title-derived dates (useful for archives but can contain release dates)
    if title:
        dt = extract_date_from_text(title)
        if dt and valid(dt):
            return format_datetime(dt)

    # 2) URL-derived dates
    if url:
        dt = extract_date_from_text(url)
        if dt and valid(dt):
            return format_datetime(dt)

    # 3) Check content
    if content:
        dt = extract_date_from_text(content)
        if dt and valid(dt):
            return format_datetime(dt)

    # 4) Prefer explicit feed/pub dates last (often refreshed timestamps)
    raw_date_str = str(raw_date_input) if raw_date_input else ""
    if raw_date_str:
        # Try RFC 2822 (Standard RSS) first using email.utils
        try:
            dt = email.utils.parsedate_to_datetime(raw_date_str)
            if valid(dt): return format_datetime(dt)
        except Exception:
            pass
            
        # Try dateutil fallback (handles ISO-8601 and others)
        try:
            # Handle common TZ abbrevs specifically for dateutil
            tzinfos = {"PST": -28800, "PDT": -25200, "EST": -18000, "EDT": -14400, "CST": -21600, "CDT": -18000, "MST": -25200, "MDT": -21600}
            dt = dateparser.parse(raw_date_str, tzinfos=tzinfos)
            if valid(dt):
                return format_datetime(dt)
        except Exception:
            pass

    # 5) Fallback sentinel
    return "0001-01-01 00:00:00"


# --- Chapters ---

def get_chapters_from_db(article_id: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT start, title, href FROM chapters WHERE article_id = ? ORDER BY start", (article_id,))
    rows = c.fetchall()
    conn.close()
    return [{"start": r[0], "title": r[1], "href": r[2]} for r in rows]

def get_chapters_batch(article_ids: list) -> dict:
    """
    Fetches chapters for multiple articles in chunks to optimize performance.
    Returns a dict: {article_id: [chapter_list]}
    """
    if not article_ids:
        return {}
    
    conn = get_connection()
    c = conn.cursor()
    chapters_map = {}
    
    # SQLite limit usually 999 vars
    chunk_size = 900
    for i in range(0, len(article_ids), chunk_size):
        chunk = article_ids[i:i+chunk_size]
        placeholders = ','.join(['?'] * len(chunk))
        c.execute(f"SELECT article_id, start, title, href FROM chapters WHERE article_id IN ({placeholders}) ORDER BY article_id, start", chunk)
        for row in c.fetchall():
            aid = row[0]
            if aid not in chapters_map: chapters_map[aid] = []
            chapters_map[aid].append({"start": row[1], "title": row[2], "href": row[3]})
            
    conn.close()
    return chapters_map

def fetch_and_store_chapters(article_id, media_url, media_type, chapter_url=None):
    """
    Fetches chapters from chapter_url (JSON) or media_url (ID3 tags).
    Stores them in DB linked to article_id.
    Returns list of chapter dicts.
    """
    # Check DB first
    existing = get_chapters_from_db(article_id)
    if existing:
        return existing

    chapters_out = []
    
    # 1) Explicit chapter URL (Podcasting 2.0)
    if chapter_url:
        try:
            resp = safe_requests_get(chapter_url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            chapters = data.get("chapters", [])
            conn = get_connection()
            c = conn.cursor()
            for ch in chapters:
                ch_id = str(uuid.uuid4())
                start = ch.get("startTime") or ch.get("start_time") or 0
                title_ch = ch.get("title", "")
                href = ch.get("url") or ch.get("link")
                c.execute("INSERT OR REPLACE INTO chapters (id, article_id, start, title, href) VALUES (?, ?, ?, ?, ?)",
                              (ch_id, article_id, float(start), title_ch, href))
                chapters_out.append({"start": float(start), "title": title_ch, "href": href})
            conn.commit()
            conn.close()
            if chapters_out:
                return chapters_out
        except Exception as e:
            log.warning(f"Chapter fetch failed for {chapter_url}: {e}")

    # 2) ID3 CHAP frames if audio
    if media_url and media_type and (media_type.startswith("audio/") or "podcast" in media_type or media_url.lower().split("?")[0].endswith("mp3")):
        try:
            from mutagen.id3 import ID3
            # Fetch first 2MB (usually enough for ID3v2 header)
            resp = safe_requests_get(media_url, headers={"Range": "bytes=0-2000000"}, timeout=12)
            if resp.ok:
                id3 = ID3(BytesIO(resp.content))
                conn = get_connection()
                c = conn.cursor()
                found_any = False
                for frame in id3.getall("CHAP"):
                    found_any = True
                    ch_id = str(uuid.uuid4())
                    start = frame.start_time / 1000.0 if frame.start_time else 0
                    title_ch = ""
                    tit2 = frame.sub_frames.get("TIT2")
                    if tit2 and tit2.text:
                        title_ch = tit2.text[0]
                    href = None
                    # Extract URL from WXXX if needed? Usually just title.
                    
                    c.execute("INSERT OR REPLACE INTO chapters (id, article_id, start, title, href) VALUES (?, ?, ?, ?, ?)",
                                  (ch_id, article_id, float(start), title_ch, href))
                    chapters_out.append({"start": float(start), "title": title_ch, "href": href})
                
                conn.commit()
                conn.close()
        except ImportError:
            log.info("mutagen not installed, skipping ID3 chapter parse.")
        except Exception as e:
            log.debug(f"ID3 chapter parse failed for {media_url}: {e}")

    return chapters_out
