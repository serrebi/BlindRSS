import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import hashlib
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from packaging.version import Version, InvalidVersion

from core.config import APP_DIR
from core.utils import safe_requests_get
from core.version import APP_VERSION
from core.update_config import (
    EXE_NAME,
    GITHUB_OWNER,
    GITHUB_REPO,
    UPDATE_ASSET_EXTENSION,
    UPDATE_MANIFEST_NAME,
)

log = logging.getLogger(__name__)

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")


def _normalize_thumbprint(value: Optional[str]) -> str:
    if not value:
        return ""
    return value.replace(" ", "").strip().upper()


def _normalize_thumbprints(values: Iterable[str]) -> Tuple[str, ...]:
    normalized = {_normalize_thumbprint(value) for value in values if value}
    normalized.discard("")
    return tuple(sorted(normalized))


def _env_thumbprints() -> Tuple[str, ...]:
    raw = os.environ.get("BLINDRSS_TRUSTED_SIGNING_THUMBPRINTS", "")
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _extract_manifest_thumbprints(payload: dict) -> Tuple[str, ...]:
    raw = payload.get("signing_thumbprints") or payload.get("signing_thumbprint")
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list):
        return tuple(str(item).strip() for item in raw if item)
    return ()


@dataclass
class UpdateInfo:
    version: Version
    tag: str
    published_at: str
    notes_summary: str
    asset_name: str
    download_url: str
    sha256: str
    signing_thumbprints: Tuple[str, ...] = ()


@dataclass
class UpdateCheckResult:
    status: str
    message: str
    info: Optional[UpdateInfo] = None


def _parse_version(value: str) -> Optional[Version]:
    if not value:
        return None
    value = str(value).strip()
    m = _SEMVER_RE.match(value)
    if not m:
        return None
    major, minor, patch = m.groups()
    normalized = f"{int(major)}.{int(minor)}.{int(patch or 0)}"
    try:
        return Version(normalized)
    except InvalidVersion:
        return None


def _format_version_tag(version: Version) -> str:
    return f"v{version.major}.{version.minor}.{version.micro}"


def _fetch_latest_release() -> Tuple[Optional[dict], Optional[str]]:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    headers = {"Accept": "application/vnd.github+json"}
    try:
        resp = safe_requests_get(url, headers=headers, timeout=15)
    except Exception as e:
        return None, f"Network error while checking GitHub: {e}"

    if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
        reset = resp.headers.get("X-RateLimit-Reset", "")
        msg = "GitHub API rate limit reached. Try again later."
        if reset:
            msg = f"{msg} Reset time (epoch): {reset}"
        return None, msg

    if not resp.ok:
        return None, f"GitHub API error: HTTP {resp.status_code}"

    try:
        return resp.json(), None
    except Exception as e:
        return None, f"Invalid GitHub response: {e}"


def _find_release_asset(release: dict, name: str) -> Optional[dict]:
    assets = release.get("assets") or []
    for asset in assets:
        if asset.get("name") == name:
            return asset
    return None


def _download_json(url: str, timeout: int = 20) -> Tuple[Optional[dict], Optional[str]]:
    try:
        resp = safe_requests_get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json(), None
    except Exception as e:
        return None, f"Failed to download update metadata: {e}"


def check_for_updates() -> UpdateCheckResult:
    current = _parse_version(APP_VERSION)
    if not current:
        return UpdateCheckResult("error", f"Invalid current version: {APP_VERSION}")

    release, err = _fetch_latest_release()
    if err:
        return UpdateCheckResult("error", err)
    if not release:
        return UpdateCheckResult("error", "No release data from GitHub.")

    tag = str(release.get("tag_name") or "").strip()
    latest = _parse_version(tag)
    if not latest:
        return UpdateCheckResult("error", f"Latest release tag is not semver: {tag}")

    if latest <= current:
        return UpdateCheckResult("up_to_date", f"BlindRSS is up to date ({_format_version_tag(current)}).")

    manifest_asset = _find_release_asset(release, UPDATE_MANIFEST_NAME)
    if not manifest_asset:
        return UpdateCheckResult("error", f"Update manifest '{UPDATE_MANIFEST_NAME}' not found in release assets.")

    manifest, err = _download_json(manifest_asset.get("browser_download_url", ""))
    if err:
        return UpdateCheckResult("error", err)
    if not manifest:
        return UpdateCheckResult("error", "Update manifest is empty.")

    manifest_version = _parse_version(str(manifest.get("version") or ""))
    if not manifest_version:
        return UpdateCheckResult("error", "Update manifest has invalid version.")
    if manifest_version != latest:
        return UpdateCheckResult("error", "Update manifest version does not match the latest release.")

    asset_name = manifest.get("asset") or manifest.get("asset_name") or ""
    if not asset_name:
        return UpdateCheckResult("error", "Update manifest is missing asset name.")
    if not asset_name.endswith(UPDATE_ASSET_EXTENSION):
        return UpdateCheckResult("error", f"Update asset must be a {UPDATE_ASSET_EXTENSION} file.")

    asset = _find_release_asset(release, asset_name)
    if not asset:
        return UpdateCheckResult("error", f"Update asset '{asset_name}' not found in release assets.")

    download_url = asset.get("browser_download_url") or manifest.get("download_url") or ""
    if not download_url:
        return UpdateCheckResult("error", "Update manifest is missing a download URL.")

    sha256 = str(manifest.get("sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return UpdateCheckResult("error", "Update manifest has an invalid SHA-256 hash.")

    notes_summary = str(manifest.get("notes_summary") or "").strip()
    published_at = str(release.get("published_at") or manifest.get("published_at") or "")
    manifest_thumbprints = _extract_manifest_thumbprints(manifest)
    allowed_thumbprints = _normalize_thumbprints(list(manifest_thumbprints) + list(_env_thumbprints()))

    info = UpdateInfo(
        version=latest,
        tag=_format_version_tag(latest),
        published_at=published_at,
        notes_summary=notes_summary,
        asset_name=asset_name,
        download_url=download_url,
        sha256=sha256,
        signing_thumbprints=allowed_thumbprints,
    )
    return UpdateCheckResult("update_available", "Update available.", info)


def is_update_supported() -> bool:
    if not getattr(sys, "frozen", False):
        return False
    helper_path = os.path.join(APP_DIR, "update_helper.bat")
    return os.path.isfile(helper_path)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_zip(zip_path: str, dest_dir: str) -> None:
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)


def _find_staging_root(extract_dir: str) -> str:
    entries = [e for e in os.listdir(extract_dir) if e and not e.startswith(".")]
    if len(entries) == 1:
        candidate = os.path.join(extract_dir, entries[0])
        if os.path.isdir(candidate):
            return candidate
    return extract_dir


def _verify_authenticode_signature(exe_path: str, allowed_thumbprints: Iterable[str]) -> Tuple[bool, str]:
    allowed = set(_normalize_thumbprints(allowed_thumbprints))
    ps_script = (
        "$ErrorActionPreference = 'Stop';"
        f"$sig = Get-AuthenticodeSignature -FilePath '{exe_path}';"
        "$subject = if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { '' };"
        "$thumb = if ($sig.SignerCertificate) { $sig.SignerCertificate.Thumbprint } else { '' };"
        "$out = @{Status=$sig.Status.ToString(); StatusMessage=$sig.StatusMessage; Subject=$subject; Thumbprint=$thumb};"
        "$out | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as e:
        return False, f"Failed to run Authenticode verification: {e}"

    if proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or "Unknown error"
        return False, f"Authenticode verification failed: {msg}"

    try:
        data = json.loads(proc.stdout.strip())
    except Exception as e:
        return False, f"Authenticode verification returned invalid data: {e}"

    status = str(data.get("Status") or "").strip()
    status_msg = str(data.get("StatusMessage") or "").strip()
    thumbprint = _normalize_thumbprint(data.get("Thumbprint"))
    if status.lower() != "valid":
        if thumbprint and thumbprint in allowed:
            return True, ""
        message = f"Signature check failed: {status} {status_msg}".strip()
        if thumbprint:
            message = f"{message} (thumbprint {thumbprint})"
        return False, message
    return True, ""


def _launch_update_helper(helper_path: str, parent_pid: int, install_dir: str, staging_root: str) -> Tuple[bool, str]:
    try:
        cmd = [
            "cmd",
            "/c",
            "start",
            "",
            helper_path,
            str(parent_pid),
            install_dir,
            staging_root,
            EXE_NAME,
        ]
        subprocess.Popen(cmd, cwd=install_dir)
        return True, ""
    except Exception as e:
        return False, f"Failed to start update helper: {e}"


def download_and_apply_update(info: UpdateInfo) -> Tuple[bool, str]:
    if not is_update_supported():
        return False, "Auto-update is only available in the packaged Windows build."

    install_dir = APP_DIR
    helper_path = os.path.join(install_dir, "update_helper.bat")
    if not os.path.isfile(helper_path):
        return False, "update_helper.bat is missing from the install directory."

    temp_root = tempfile.mkdtemp(prefix="BlindRSS_update_")
    zip_path = os.path.join(temp_root, info.asset_name)
    extract_dir = os.path.join(temp_root, "extract")
    os.makedirs(extract_dir, exist_ok=True)

    try:
        resp = safe_requests_get(info.download_url, stream=True, timeout=30)
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 512):
                if chunk:
                    f.write(chunk)
    except Exception as e:
        return False, f"Failed to download update: {e}"

    digest = _sha256_file(zip_path)
    if digest.lower() != info.sha256.lower():
        return False, "Downloaded update failed SHA-256 verification."

    try:
        _extract_zip(zip_path, extract_dir)
    except Exception as e:
        return False, f"Failed to extract update: {e}"

    staging_root = _find_staging_root(extract_dir)
    exe_path = os.path.join(staging_root, EXE_NAME)
    if not os.path.isfile(exe_path):
        return False, f"Update package is missing {EXE_NAME}."

    ok, msg = _verify_authenticode_signature(exe_path, info.signing_thumbprints)
    if not ok:
        return False, msg

    helper_run_path = helper_path
    try:
        helper_temp = os.path.join(temp_root, "update_helper.bat")
        shutil.copy2(helper_path, helper_temp)
        helper_run_path = helper_temp
    except Exception:
        helper_run_path = helper_path

    ok, msg = _launch_update_helper(helper_run_path, os.getpid(), install_dir, staging_root)
    if not ok:
        return False, msg

    return True, "Update prepared. The app will restart after it exits."
