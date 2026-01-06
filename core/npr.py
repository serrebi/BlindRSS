import re
import json
import logging
from core import utils

log = logging.getLogger(__name__)

def is_npr_url(url: str) -> bool:
    if not url:
        return False
    return "npr.org" in url.lower()

def extract_npr_audio(url: str, timeout_s: float = 10.0) -> tuple[str | None, str | None]:
    """
    Extracts the audio URL and type from an NPR story page.
    Returns (audio_url, audio_type).
    """
    if not is_npr_url(url):
        return None, None
        
    try:
        resp = utils.safe_requests_get(url, timeout=timeout_s)
        resp.raise_for_status()
        html = resp.text
        
        # 1. Try finding data-audio JSON in Brightspot (new NPR CMS)
        # It looks like: data-audio='{"audioUrl":"...","duration":...}'
        match = re.search(r'data-audio=\'({.*?})\'', html)
        if match:
            try:
                data = json.loads(match.group(1))
                audio_url = data.get('audioUrl')
                if audio_url:
                    # Unescape backslashes if any
                    audio_url = audio_url.replace('\\/', '/')
                    return audio_url, "audio/mpeg"
            except Exception as e:
                log.debug(f"NPR data-audio JSON parse failed: {e}")
                
        # 2. Try finding download link
        # It looks like: <li class="audio-tool audio-tool-download"><a href="..."
        match = re.search(r'href="(https://ondemand.npr.org/anon.npr-mp3/.*?\.mp3.*?)"', html)
        if match:
            audio_url = match.group(1).replace('&amp;', '&')
            return audio_url, "audio/mpeg"
            
        # 3. Fallback: search for any ondemand.npr.org mp3 link
        match = re.search(r'(https://ondemand.npr.org/[^"].*\.mp3[^"].*)"', html)
        if match:
            return match.group(1).replace('&amp;', '&'), "audio/mpeg"

    except Exception as e:
        log.warning(f"NPR audio extraction failed for {url}: {e}")
        
    return None, None
