"""
Range-aware local HTTP proxy with on-disk caching to improve seek performance on
high-latency remote HTTP/HTTPS audio files.

Why this exists:
- VLC seeks over HTTP using Range requests.
- On high-latency connections, each seek can pause while VLC re-requests remote bytes.
- This proxy terminates VLC's HTTP requests locally and serves bytes from a local cache.
- Cache misses are fetched from the origin using larger "prefetch" ranges and stored on disk.

Design notes:
- Cache is stored as chunk files per URL to avoid creating huge sparse files when seeking far ahead.
- Uses requests.Session to reuse TCP/TLS connections (keep-alive) for better latency.
- Provides a /health endpoint so callers can reliably wait for startup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import traceback
import os
import re
import threading
import time
import tempfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

LOG = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

# Cap how much extra we fetch inline (beyond the requested bytes) to keep seeks snappy.
# Larger amounts still happen via background download.
_INLINE_PREFETCH_CAP_BYTES = 2 * 1024 * 1024

_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d+)?$")


def _safe_mkdir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "ignore")).hexdigest()


def _merge_segments(segs: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if not segs:
        return []
    segs = sorted(segs, key=lambda x: (x[0], x[1]))
    out: List[Tuple[int, int]] = []
    cs, ce = segs[0]
    for s, e in segs[1:]:
        if s <= ce + 1:
            ce = max(ce, e)
        else:
            out.append((cs, ce))
            cs, ce = s, e
    out.append((cs, ce))
    return out


def _normalize_segments(segs: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Normalize on-disk chunk segments.

    Segments correspond 1:1 with cache files named '<start>-<end>.bin'.
    Do NOT merge ranges here, or metadata may point at non-existent files.
    """
    out = set()
    for s, e in (segs or []):
        try:
            s = int(s)
            e = int(e)
        except Exception:
            continue
        if e < s:
            continue
        out.add((s, e))
    return sorted(out, key=lambda x: (x[0], x[1]))


def _missing_segments(have: List[Tuple[int, int]], start: int, end: int) -> List[Tuple[int, int]]:
    if start > end:
        return []
    have = _merge_segments(have)
    missing: List[Tuple[int, int]] = []
    cur = start
    for s, e in have:
        if e < cur:
            continue
        if s > end:
            break
        if s > cur:
            missing.append((cur, min(end, s - 1)))
        cur = max(cur, e + 1)
        if cur > end:
            break
    if cur <= end:
        missing.append((cur, end))
    return missing


def _parse_content_range(value: str) -> Optional[Tuple[int, int, Optional[int]]]:
    # Example: "bytes 0-0/12345" or "bytes 0-0/*"
    if not value:
        return None
    m = re.match(r"^\s*bytes\s+(\d+)-(\d+)/(\d+|\*)\s*$", value, re.IGNORECASE)
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2))
    total_raw = m.group(3)
    total = None if total_raw == "*" else int(total_raw)
    return a, b, total


def _parse_range_header(range_value: str, total_length: Optional[int]) -> Optional[Tuple[int, int]]:
    # Supports: bytes=start-end, bytes=start-
    if not range_value:
        return None
    range_value = range_value.strip()
    m = _RANGE_RE.match(range_value)
    if not m:
        return None
    start = int(m.group(1))
    end_s = m.group(2)
    if end_s is None or end_s == "":
        if total_length is None:
            # Unknown length: serve a reasonable window starting at 'start'
            return (start, start + (2 * 1024 * 1024) - 1)
        return (start, max(start, total_length - 1))
    end = int(end_s)
    if end < start:
        end = start
    if total_length is not None:
        end = min(end, max(start, total_length - 1))
    return (start, end)


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 256


@dataclass
class _Entry:
    url: str
    headers: Dict[str, str]
    cache_dir: str
    prefetch_bytes: int
    background_download: bool
    background_chunk_bytes: int

    session: requests.Session = field(default_factory=requests.Session)
    total_length: Optional[int] = None
    content_type: str = "application/octet-stream"
    range_supported: Optional[bool] = None
    segments: List[Tuple[int, int]] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)
    last_access: float = field(default_factory=time.time)

    _dir: str = ""
    _bg_thread: Optional[threading.Thread] = None
    _bg_stop: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        _safe_mkdir(self.cache_dir)
        self._dir = os.path.join(self.cache_dir, _sha256_hex(self.url))
        _safe_mkdir(self._dir)
        self._load_existing_segments()

        # A slightly more robust session for high-latency connections.
        try:
            adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=2)
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)
        except Exception:
            pass

    def touch(self) -> None:
        self.last_access = time.time()

    def _chunk_path(self, start: int, end: int) -> str:
        return os.path.join(self._dir, f"{start:012d}-{end:012d}.bin")

    def _load_existing_segments(self) -> None:
        segs: List[Tuple[int, int]] = []
        try:
            for name in os.listdir(self._dir):
                m = re.match(r"^(\d+)-(\d+)\.bin$", name)
                if not m:
                    continue
                s = int(m.group(1))
                e = int(m.group(2))
                if e >= s:
                    segs.append((s, e))
        except Exception:
            pass
        self.segments = _normalize_segments(segs)


    def _segment_file_is_valid(self, s: int, e: int) -> bool:
        path = self._chunk_path(s, e)
        expected = (e - s + 1)
        if expected <= 0:
            return False
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return False
        except Exception:
            return False
        return st.st_size == expected

    def _prune_bad_segments(self) -> None:
        """
        Drop segment metadata that points at missing or truncated files.

        This prevents HTTP 500 errors if a cache file disappears (temp cleanup,
        interrupted write, antivirus, etc.).
        """
        try:
            with self.lock:
                bad: List[Tuple[int, int]] = []
                for s, e in list(self.segments):
                    if not self._segment_file_is_valid(s, e):
                        bad.append((s, e))
                if not bad:
                    return
                self.segments = [seg for seg in self.segments if seg not in bad]
                self.segments = _normalize_segments(self.segments)
            # Best-effort cleanup of corrupt/missing files.
            for s, e in bad:
                try:
                    os.remove(self._chunk_path(s, e))
                except Exception:
                    pass
        except Exception:
            pass

    def probe(self) -> None:
        if self.range_supported is not None and (self.total_length is not None or self.range_supported is False):
            return

        hdrs = dict(self.headers or {})
        hdrs.setdefault("User-Agent", _DEFAULT_UA)
        hdrs.setdefault("Accept", "*/*")
        # Avoid transparent compression; ranged fetches must be byte-exact.
        hdrs.setdefault("Accept-Encoding", "identity")
        # Avoid gzip/deflate so byte ranges always map 1:1 to the original file.
        hdrs.setdefault("Accept-Encoding", "identity")

        # Try a single-byte range request (most reliable way to learn length + range support)
        hdrs_probe = dict(hdrs)
        hdrs_probe["Range"] = "bytes=0-0"

        try:
            r = self.session.get(self.url, headers=hdrs_probe, stream=True, timeout=(10, 30), allow_redirects=True)
        except Exception as e:
            LOG.warning("RangeCacheProxy probe failed: %s", e)
            self.range_supported = False
            self.total_length = None
            return

        try:
            ct = r.headers.get("Content-Type") or ""
            if ct:
                self.content_type = ct.split(";")[0].strip() or self.content_type

            if r.status_code == 206:
                cr = r.headers.get("Content-Range", "")
                parsed = _parse_content_range(cr)
                if parsed:
                    _, _, total = parsed
                    self.total_length = total
                else:
                    # Fallback to Content-Length; may be 1 for this response.
                    try:
                        cl = int(r.headers.get("Content-Length", "0"))
                        self.total_length = max(self.total_length or 0, cl)
                    except Exception:
                        pass
                self.range_supported = True
            elif r.status_code == 200:
                self.range_supported = False
                try:
                    cl = int(r.headers.get("Content-Length", "0"))
                    if cl > 0:
                        self.total_length = cl
                except Exception:
                    pass
            else:
                # Some servers respond 416, 403, etc.
                self.range_supported = False
        finally:
            try:
                r.close()
            except Exception:
                pass

    def _fetch_range(self, start: int, end: int) -> bool:
        # Fetch start-end inclusive from origin and store as a chunk file.
        hdrs = dict(self.headers or {})
        hdrs.setdefault("User-Agent", _DEFAULT_UA)
        hdrs.setdefault("Accept", "*/*")
        hdrs.setdefault("Accept-Encoding", "identity")
        hdrs["Range"] = f"bytes={start}-{end}"

        try:
            r = self.session.get(self.url, headers=hdrs, stream=True, timeout=(10, 60), allow_redirects=True)
        except Exception as e:
            LOG.warning("RangeCacheProxy fetch failed: %s", e)
            return False

        try:
            if r.status_code == 200:
                # Origin ignored Range and returned full body. Do not cache this as a ranged chunk.
                self.range_supported = False
                return False
            if r.status_code != 206:
                return False

            # Try to determine actual served range (important if origin clamps end).
            served_start, served_end = start, end
            if r.status_code == 206:
                cr = r.headers.get("Content-Range", "")
                parsed = _parse_content_range(cr)
                if parsed:
                    served_start, served_end, total = parsed
                    if total is not None:
                        self.total_length = total
            else:
                # Full-body 200 - treat as no range support
                self.range_supported = False


            expected_len = (served_end - served_start + 1)
            if expected_len <= 0:
                return False

            tmp_path = os.path.join(self._dir, f".tmp_{served_start}_{served_end}_{int(time.time()*1000)}")
            bytes_written = 0
            try:
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=512 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        bytes_written += len(chunk)
            except Exception:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                return False

            if bytes_written != expected_len:
                # Interrupted / truncated fetch. Do not register as cached.
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                return False

            final_path = self._chunk_path(served_start, served_end)
            try:
                if os.path.exists(final_path):
                    # If already cached, keep existing.
                    os.remove(tmp_path)
                else:
                    os.replace(tmp_path, final_path)
            except Exception:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                return False

            self.segments.append((served_start, served_end))
            self.segments = _normalize_segments(self.segments)

            # Update metadata if possible
            if self.content_type == "application/octet-stream":
                ct = r.headers.get("Content-Type") or ""
                if ct:
                    self.content_type = ct.split(";")[0].strip() or self.content_type

            return True
        finally:
            try:
                r.close()
            except Exception:
                pass

    def _read_from_cache(self, start: int, end: int) -> Tuple[int, bytes]:
        # Return (served_end, bytes). Assumes the requested interval is fully cached.
        # Reads from the actual chunk files on disk.
        # NOTE: self.segments must reflect real files; do NOT iterate over merged coverage.
        self._prune_bad_segments()
        segs = list(self.segments)
        needed_start = start
        out = bytearray()

        while needed_start <= end:
            # Choose the cached chunk that covers needed_start and extends farthest.
            best = None
            best_end = -1
            for s, e in segs:
                if s <= needed_start <= e and e > best_end:
                    best = (s, e)
                    best_end = e
            if best is None:
                raise IOError("Cache miss while reading")

            s, e = best
            part_start = needed_start
            part_end = min(e, end)
            expected = part_end - part_start + 1
            if expected <= 0:
                raise IOError("Cache miss while reading")
            path = self._chunk_path(s, e)
            try:
                with open(path, "rb") as f:
                    f.seek(part_start - s)
                    data = f.read(expected)
            except FileNotFoundError:
                raise IOError("Cache miss while reading")
            except Exception as ex:
                raise IOError(f"Cache read failed: {ex}") from ex
            if len(data) != expected:
                raise IOError("Cache miss while reading")
            out.extend(data)
            needed_start = part_end + 1

        served_end = needed_start - 1
        if served_end < start:
            raise IOError("Cache miss while reading")
        return served_end, bytes(out)

    def ensure_cached(self, start: int, end: int) -> int:
        """
        Ensure bytes [start..end] are available in cache (best effort).
        Returns the maximum contiguous cached end >= start after fetching.
        """
        self.probe()
        if self.range_supported is False:
            return start - 1
        self._prune_bad_segments()


        # Compute a *capped* prefetch end. Big read-ahead happens in background,
        # but we still want a small inline cushion to reduce immediate follow-up requests.
        want_end = end
        extra = min(int(self.prefetch_bytes), _INLINE_PREFETCH_CAP_BYTES)
        if extra > 0:
            if self.total_length is not None:
                want_end = min(self.total_length - 1, end + extra)
            else:
                want_end = end + extra

        missing = _missing_segments(self.segments, start, want_end)

        # Fetch missing intervals. Cap number of fetches per request to avoid runaway loops.
        max_fetches = 12
        for (ms, me) in missing[:max_fetches]:
            if self.total_length is not None:
                me = min(me, self.total_length - 1)
            if me < ms:
                continue
            ok = self._fetch_range(ms, me)
            if not ok:
                break

        # Determine contiguous coverage from start
        have = _merge_segments(self.segments)
        served_end = start - 1
        for s, e in have:
            if s <= start <= e:
                served_end = e
                break
            if s > start:
                break
        return served_end

    def maybe_start_background_download(self) -> None:
        if not self.background_download:
            return
        if self._bg_thread and self._bg_thread.is_alive():
            return
        self._bg_stop.clear()

        def run() -> None:
            try:
                self.probe()
                if self.range_supported is False:
                    return
                # Download forward from current max cached end.
                while not self._bg_stop.is_set():
                    # Stop if idle for a while.
                    if time.time() - self.last_access > 120:
                        return

                    with self.lock:
                        self._prune_bad_segments()
                        have = _merge_segments(self.segments)
                        cur_end = -1
                        if have:
                            cur_end = have[-1][1]
                        start = max(0, cur_end + 1)

                        if self.total_length is not None and start >= self.total_length:
                            return

                        end = start + self.background_chunk_bytes - 1
                        if self.total_length is not None:
                            end = min(end, self.total_length - 1)

                        # If already cached (race), skip forward.
                        miss = _missing_segments(self.segments, start, end)
                        if not miss:
                            # advance a bit
                            time.sleep(0.05)
                            continue

                        ms, me = miss[0]
                        ok = self._fetch_range(ms, me)
                        if not ok:
                            # back off on errors
                            time.sleep(0.5)
                            continue

                    # Small pause to avoid pegging CPU
                    time.sleep(0.02)
            except Exception as e:
                LOG.debug("Background download stopped: %s", e)

        self._bg_thread = threading.Thread(target=run, name="RangeCacheProxyBG", daemon=True)
        self._bg_thread.start()

    def stop_background(self) -> None:
        try:
            self._bg_stop.set()
        except Exception:
            pass


_RANGE_PROXY_SINGLETON: Optional["RangeCacheProxy"] = None


class RangeCacheProxy:
    def __init__(
        self,
        cache_dir: Optional[str] = None,
        prefetch_kb: int = 16384,
        background_download: bool = True,
        background_chunk_kb: int = 8192,
        inline_window_kb: int = 1024,
    ):
        base = cache_dir or os.path.join(tempfile.gettempdir(), "BlindRSS_streamcache")
        _safe_mkdir(base)
        self.cache_dir = base
        self.prefetch_bytes = max(512 * 1024, int(prefetch_kb) * 1024)
        # For low-latency seeking: never block a single VLC request on huge prefetch.
        # We may still download ahead in the background.
        self.inline_window_bytes = max(256 * 1024, int(inline_window_kb) * 1024)
        self.max_inline_prefetch_bytes = 2 * 1024 * 1024
        self.background_download = bool(background_download)
        self.background_chunk_bytes = max(1024 * 1024, int(background_chunk_kb) * 1024)

        self._entries: Dict[str, _Entry] = {}
        self._lock = threading.RLock()

        self._server: Optional[_ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._host = "127.0.0.1"
        self._port: Optional[int] = None
        # Once a port is chosen, try to reuse it on restarts so existing MRLs don't break.
        self._preferred_port: Optional[int] = None
        self._ready = threading.Event()

        self._map_dir = os.path.join(self.cache_dir, "mappings")
        _safe_mkdir(self._map_dir)

    def _mapping_path(self, sid: str) -> str:
        return os.path.join(self._map_dir, f"{sid}.json")

    def _save_mapping(self, sid: str, url: str, headers: Optional[Dict[str, str]]) -> None:
        try:
            _safe_mkdir(self._map_dir)
            tmp = self._mapping_path(sid) + ".tmp"
            payload = {"url": url, "headers": headers or {}}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, self._mapping_path(sid))
        except Exception:
            pass

    def _load_mapping(self, sid: str) -> Optional[Dict[str, object]]:
        try:
            path = self._mapping_path(sid)
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if not isinstance(obj, dict):
                return None
            url = obj.get("url")
            if not isinstance(url, str) or not url:
                return None
            headers = obj.get("headers")
            if not isinstance(headers, dict):
                headers = {}
            # Force keys/values to strings
            safe_headers: Dict[str, str] = {}
            for k, v in headers.items():
                try:
                    safe_headers[str(k)] = str(v)
                except Exception:
                    continue
            return {"url": url, "headers": safe_headers}
        except Exception:
            return None

    def _get_or_create_entry(self, sid: str, url: str, headers: Optional[Dict[str, str]]) -> _Entry:
        with self._lock:
            ent = self._entries.get(sid)
            if ent is not None:
                return ent
            ent = _Entry(
                url=url,
                headers=headers or {},
                cache_dir=self.cache_dir,
                prefetch_bytes=self.prefetch_bytes,
                background_download=self.background_download,
                background_chunk_bytes=self.background_chunk_bytes,
            )
            self._entries[sid] = ent
            return ent

    def start(self) -> None:
        """Start the local HTTP server.

        Important: never restart an *alive* server from here.

        VLC streams can stay connected for a long time to a single
        http://127.0.0.1:<port>/media?id=... URL. If we stop/rebind the server
        while VLC is still using that URL, VLC will log:
            "cannot connect to 127.0.0.1:<port>"

        So we only restart if the server thread is actually dead.
        """

        # Fast path: server already running.
        with self._lock:
            if self._server is not None and self._thread is not None and self._thread.is_alive():
                # Best-effort readiness check (do not restart on failure).
                pass

        if self._server is not None and self._thread is not None and self._thread.is_alive():
            try:
                self._wait_ready(timeout=1.0)
            except Exception:
                pass
            return

        # If the server exists but the thread is dead, ensure it's fully stopped.
        with self._lock:
            if self._server is not None:
                try:
                    self.stop()
                except Exception:
                    pass

        with self._lock:
            self._ready.clear()
            proxy = self

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def log_message(self, fmt: str, *args) -> None:
                    # silence
                    try:
                        LOG.debug("RangeCacheProxy: " + fmt, *args)
                    except Exception:
                        pass

                def _send_health(self) -> None:
                    body = b"ok"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except Exception:
                        pass

                def do_HEAD(self) -> None:
                    parsed = urlparse(self.path)
                    if parsed.path == "/health":
                        proxy._ready.set()
                        self._send_health()
                        return
                    if parsed.path != "/media":
                        self.send_error(404, "Not Found")
                        return
                    q = parse_qs(parsed.query)
                    sid = q.get("id", [None])[0]
                    if not sid:
                        self.send_error(404, "Not Found")
                        return
                    with proxy._lock:
                        ent = proxy._entries.get(sid)
                    if not ent:
                        info = proxy._load_mapping(sid)
                        if info is not None:
                            ent = proxy._get_or_create_entry(sid, str(info["url"]), dict(info.get("headers") or {}))
                    if not ent:
                        self.send_error(404, "Not Found")
                        return
                    with ent.lock:
                        ent.touch()
                        ent.probe()
                        self.send_response(200)
                        self.send_header("Content-Type", ent.content_type)
                        if ent.total_length is not None:
                            self.send_header("Content-Length", str(ent.total_length))
                        if ent.range_supported:
                            self.send_header("Accept-Ranges", "bytes")
                        self.end_headers()

                def do_GET(self) -> None:
                    parsed = urlparse(self.path)
                    if parsed.path == "/health":
                        proxy._ready.set()
                        self._send_health()
                        return
                    if parsed.path != "/media":
                        self.send_error(404, "Not Found")
                        return
                    q = parse_qs(parsed.query)
                    sid = q.get("id", [None])[0]
                    if not sid:
                        self.send_error(404, "Not Found")
                        return
                    with proxy._lock:
                        ent = proxy._entries.get(sid)
                    if not ent:
                        info = proxy._load_mapping(sid)
                        if info is not None:
                            ent = proxy._get_or_create_entry(sid, str(info["url"]), dict(info.get("headers") or {}))
                    if not ent:
                        self.send_error(404, "Not Found")
                        return

                    with ent.lock:
                        ent.touch()
                        ent.probe()

                        # If origin does not support range, just stream through (no caching)
                        if ent.range_supported is False:
                            hdrs = dict(ent.headers or {})
                            hdrs.setdefault("User-Agent", _DEFAULT_UA)
                            hdrs.setdefault("Accept", "*/*")
                            hdrs.setdefault("Accept-Encoding", "identity")
                            try:
                                r = ent.session.get(ent.url, headers=hdrs, stream=True, timeout=(10, 60), allow_redirects=True)
                            except Exception as e:
                                self.send_error(502, f"Origin fetch failed: {e}")
                                return
                            try:
                                self.send_response(r.status_code)
                                for k, v in r.headers.items():
                                    lk = k.lower()
                                    if lk in ("transfer-encoding", "connection", "keep-alive", "proxy-authenticate",
                                              "proxy-authorization", "te", "trailers", "upgrade"):
                                        continue
                                    self.send_header(k, v)
                                self.end_headers()
                                for chunk in r.iter_content(chunk_size=256 * 1024):
                                    if not chunk:
                                        continue
                                    self.wfile.write(chunk)
                                return
                            finally:
                                try:
                                    r.close()
                                except Exception:
                                    pass

                        # Range request handling (preferred)
                        rng = self.headers.get("Range", "")
                        start_end = _parse_range_header(rng, ent.total_length)
                        if start_end is None:
                            # VLC should send Range for seeks; if not, serve from 0 as best effort
                            if ent.total_length is not None:
                                start_end = (0, ent.total_length - 1)
                            else:
                                start_end = (0, max(0, proxy.inline_window_bytes - 1))
                        start, end = start_end

                        if ent.total_length is not None:
                            end = min(end, ent.total_length - 1)

                        # Keep response size bounded for low-latency. VLC will ask for more.
                        reply_end = min(end, start + max(1, proxy.inline_window_bytes) - 1)

                        # Ensure cache is filled (best effort). Avoid huge inline prefetch.
                        served_end = ent.ensure_cached(start, reply_end)

                        if served_end < start:
                            # Could not fetch even the start byte. Last resort: passthrough range from origin.
                            hdrs = dict(ent.headers or {})
                            hdrs.setdefault("User-Agent", _DEFAULT_UA)
                            hdrs.setdefault("Accept", "*/*")
                            hdrs.setdefault("Accept-Encoding", "identity")
                            hdrs["Range"] = f"bytes={start}-{reply_end}"
                            try:
                                r = ent.session.get(ent.url, headers=hdrs, stream=True, timeout=(10, 60), allow_redirects=True)
                            except Exception as e:
                                self.send_error(502, f"Origin fetch failed: {e}")
                                return
                            try:
                                if r.status_code not in (200, 206):
                                    self.send_error(502, f"Origin status {r.status_code}")
                                    return
                                self.send_response(206 if r.status_code == 206 else 200)
                                ct = r.headers.get("Content-Type") or ent.content_type
                                self.send_header("Content-Type", ct)
                                for k, v in r.headers.items():
                                    lk = k.lower()
                                    if lk in ("transfer-encoding", "connection", "keep-alive", "proxy-authenticate",
                                              "proxy-authorization", "te", "trailers", "upgrade"):
                                        continue
                                    if lk in ("content-type",):
                                        continue
                                    self.send_header(k, v)
                                self.end_headers()
                                for chunk in r.iter_content(chunk_size=256 * 1024):
                                    if not chunk:
                                        continue
                                    self.wfile.write(chunk)
                                return
                            finally:
                                try:
                                    r.close()
                                except Exception:
                                    pass

                        actual_end = min(reply_end, served_end)

                        data = None
                        for _attempt in range(2):
                            try:
                                ent._prune_bad_segments()
                                _, data = ent._read_from_cache(start, actual_end)
                                break
                            except Exception:
                                # Cache might be inconsistent if an earlier fetch was interrupted, or if the
                                # temp cache was cleaned. Reload metadata, prune bad segments, and try to re-fetch.
                                try:
                                    ent._load_existing_segments()
                                    ent._prune_bad_segments()
                                except Exception:
                                    pass
                                try:
                                    served_end = ent.ensure_cached(start, actual_end)
                                    if served_end < start:
                                        data = None
                                        break
                                    actual_end = min(actual_end, served_end)
                                except Exception:
                                    data = None
                                    break

                        if data is None:
                            # Last resort: passthrough range from origin (keeps playback alive even if cache is broken).
                            end_for_passthrough = max(start, actual_end)
                            hdrs = dict(ent.headers or {})
                            hdrs.setdefault("User-Agent", _DEFAULT_UA)
                            hdrs.setdefault("Accept", "*/*")
                            hdrs.setdefault("Accept-Encoding", "identity")
                            hdrs["Range"] = f"bytes={start}-{end_for_passthrough}"
                            try:
                                r = ent.session.get(ent.url, headers=hdrs, stream=True, timeout=(10, 60), allow_redirects=True)
                            except Exception as e:
                                self.send_error(502, f"Origin fetch failed: {e}")
                                return
                            try:
                                self.send_response(r.status_code)
                                for k, v in r.headers.items():
                                    lk = k.lower()
                                    if lk in ("transfer-encoding", "connection", "keep-alive", "proxy-authenticate",
                                              "proxy-authorization", "te", "trailers", "upgrade"):
                                        continue
                                    self.send_header(k, v)
                                self.end_headers()
                                for chunk in r.iter_content(chunk_size=256 * 1024):
                                    if not chunk:
                                        continue
                                    self.wfile.write(chunk)
                                return
                            finally:
                                try:
                                    r.close()
                                except Exception:
                                    pass

                        # Start background downloader (makes later seeks much faster)
                        try:
                            ent.maybe_start_background_download()
                        except Exception:
                            pass

                        # Respond 206 Partial Content
                        self.send_response(206)
                        self.send_header("Content-Type", ent.content_type)
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Length", str(len(data)))
                        if ent.total_length is not None:
                            self.send_header("Content-Range", f"bytes {start}-{start + len(data) - 1}/{ent.total_length}")
                        else:
                            self.send_header("Content-Range", f"bytes {start}-{start + len(data) - 1}/*")
                        self.end_headers()
                        try:
                            self.wfile.write(data)
                        except Exception:
                            pass

            # Bind and start. Prefer reusing the same port across restarts.
            bound = False
            if self._preferred_port is not None:
                try:
                    self._server = _ThreadingHTTPServer((self._host, int(self._preferred_port)), Handler)
                    bound = True
                except Exception:
                    self._server = None
                    bound = False
            if not bound:
                self._server = _ThreadingHTTPServer((self._host, 0), Handler)
            self._port = self._server.server_address[1]
            if self._preferred_port is None:
                self._preferred_port = self._port

            def run() -> None:
                try:
                    self._server.serve_forever(poll_interval=0.25)
                except Exception as e:
                    LOG.warning("RangeCacheProxy server error: %s\n%s", e, traceback.format_exc())
                finally:
                    # Mark as not ready if the server stops unexpectedly.
                    try:
                        self._ready.clear()
                    except Exception:
                        pass

            self._thread = threading.Thread(target=run, name="RangeCacheProxy", daemon=True)
            self._thread.start()

        # Wait until responding
        self._wait_ready(timeout=2.0)

    def stop(self) -> None:
        with self._lock:
            if self._server is None:
                return
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None
            self._thread = None
            self._port = None
            self._ready.clear()

    def _wait_ready(self, timeout: float = 2.0) -> bool:
        import http.client
        deadline = time.time() + max(0.1, float(timeout))
        while time.time() < deadline:
            with self._lock:
                if self._port is None:
                    time.sleep(0.05)
                    continue
                port = self._port
            try:
                conn = http.client.HTTPConnection(self._host, port, timeout=0.5)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                try:
                    _ = resp.read()
                except Exception:
                    pass
                ok = (resp.status == 200)
                try:
                    conn.close()
                except Exception:
                    pass
                if ok:
                    self._ready.set()
                    return True
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
            time.sleep(0.05)
        return False

    def is_ready(self) -> bool:
        # Active health check (the server may have died while the event is still set).
        # Do not clear the readiness event on a single failure; transient hiccups
        # should not trigger restarts that break in-flight VLC connections.
        try:
            return bool(self._wait_ready(timeout=0.25))
        except Exception:
            return False

    @property
    def base_url(self) -> str:
        self.start()
        try:
            self._ready.wait(timeout=2.0)
        except Exception:
            pass
        with self._lock:
            if self._port is None:
                raise RuntimeError("RangeCacheProxy not started")
            return f"http://{self._host}:{self._port}"

    def proxify(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        """
        Register a URL and return a local proxy URL.

        The id is a short stable hash of (url + headers subset). Using a stable id
        allows VLC retries without re-registering, while keeping cache per URL.
        """
        if not url:
            return url
        self.start()

        # Include headers in id because some hosts require specific Referer/User-Agent
        # to permit range access.
        h = headers or {}
        id_src = url + "\n" + "\n".join(f"{k.lower()}:{v}" for k, v in sorted(h.items(), key=lambda kv: kv[0].lower()))
        sid = _sha256_hex(id_src)[:24]

        # Persist the mapping so /media can still resolve even if the in-memory entry is missing.
        self._save_mapping(sid, url, headers)

        ent = self._get_or_create_entry(sid, url, headers)
        # ensure probe in background (best effort)
        try:
            with ent.lock:
                ent.probe()
        except Exception:
            pass

        return f"{self.base_url}/media?id={sid}"

    def prune(self, max_entries: int = 20, max_idle_seconds: int = 1800) -> None:
        # Optional: drop very old entries from memory.
        now = time.time()
        with self._lock:
            items = list(self._entries.items())
            items.sort(key=lambda kv: kv[1].last_access)
            # Remove idle
            for sid, ent in items:
                if len(self._entries) <= max_entries:
                    break
                if now - ent.last_access < max_idle_seconds:
                    continue
                try:
                    ent.stop_background()
                except Exception:
                    pass
                self._entries.pop(sid, None)


def get_range_cache_proxy(
    cache_dir: Optional[str] = None,
    prefetch_kb: int = 16384,
    background_download: bool = True,
    background_chunk_kb: int = 8192,
    inline_window_kb: int = 1024,
) -> RangeCacheProxy:
    global _RANGE_PROXY_SINGLETON
    if _RANGE_PROXY_SINGLETON is None:
        _RANGE_PROXY_SINGLETON = RangeCacheProxy(
            cache_dir=cache_dir,
            prefetch_kb=prefetch_kb,
            background_download=background_download,
            background_chunk_kb=background_chunk_kb,
            inline_window_kb=inline_window_kb,
        )
    else:
        # Allow tuning without replacing the server
        try:
            if cache_dir:
                _RANGE_PROXY_SINGLETON.cache_dir = cache_dir
                try:
                    _RANGE_PROXY_SINGLETON._map_dir = os.path.join(cache_dir, "mappings")
                    _safe_mkdir(_RANGE_PROXY_SINGLETON._map_dir)
                except Exception:
                    pass
            if prefetch_kb:
                _RANGE_PROXY_SINGLETON.prefetch_bytes = max(512 * 1024, int(prefetch_kb) * 1024)
            if inline_window_kb:
                _RANGE_PROXY_SINGLETON.inline_window_bytes = max(256 * 1024, int(inline_window_kb) * 1024)
            _RANGE_PROXY_SINGLETON.background_download = bool(background_download)
            if background_chunk_kb:
                _RANGE_PROXY_SINGLETON.background_chunk_bytes = max(1024 * 1024, int(background_chunk_kb) * 1024)
        except Exception:
            pass
    return _RANGE_PROXY_SINGLETON


