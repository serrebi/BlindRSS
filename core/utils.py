import requests
import re
import uuid
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
from io import BytesIO
from core.db import get_connection

log = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/rss+xml,application/xml,application/atom+xml,text/xml;q=0.9,*/*;q=0.8'
}

def safe_requests_get(url, **kwargs):
    """Wrapper for requests.get with default browser headers."""
    headers = kwargs.pop("headers", {})
    # Merge with defaults, preserving caller's headers if they exist
    final_headers = HEADERS.copy()
    final_headers.update(headers)
    return requests.get(url, headers=final_headers, **kwargs)

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
    # 2) ISO-like yyyy-mm-dd
    m_iso = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m_iso:
        try:
            y, mth, d = map(int, m_iso.groups())
            return datetime(y, mth, d)
        except Exception:
            pass
    # 3) Month name forms (e.g., May 17 2021)
    try:
        dt = dateparser.parse(text, fuzzy=True, default=datetime(1900, 1, 1))
        if dt.year > 1900:
            return dt
    except Exception:
        pass
    return None

def normalize_date(raw_date_str: str, title: str = "", content: str = "", url: str = "") -> str:
    """
    Robust date normalizer.
    Prioritizes dates found explicitly in the Title or URL, as some feeds (e.g. archives) 
    put the original air date there while using a recent timestamp for pubDate.
    """
    now = datetime.now(timezone.utc)
    
    def valid(dt: datetime) -> bool:
        if not dt: return False
        if dt.tzinfo:
            dt_cmp = dt.astimezone(timezone.utc)
        else:
            dt_cmp = dt.replace(tzinfo=timezone.utc)
        # discard if more than 2 days in future (some timezones are ahead)
        return (dt_cmp - now) <= timedelta(days=2)

    # 1) Check Title first (highest priority for archives)
    if title:
        dt = extract_date_from_text(title)
        if dt and valid(dt):
            return format_datetime(dt)

    # 2) Check URL
    if url:
        dt = extract_date_from_text(url)
        if dt and valid(dt):
            return format_datetime(dt)

    # 3) Check raw feed date
    if raw_date_str:
        try:
            dt = dateparser.parse(raw_date_str)
            if valid(dt):
                return format_datetime(dt)
        except Exception:
            pass

    # 4) Check content
    if content:
        dt = extract_date_from_text(content)
        if dt and valid(dt):
            return format_datetime(dt)

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
    if media_url and media_type and (media_type.startswith("audio/") or "podcast" in media_type or media_url.endswith("mp3")):
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
            log.info(f"ID3 chapter parse failed for {media_url}: {e}")

    return chapters_out
