"""Polite HTTP client: honest user-agent, per-host rate limiting, on-disk cache, size limits."""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)
_last_hit: dict[str, float] = {}
_lock = threading.Lock()

ALLOWED_DOC_HOSTS = {
    "www.bseindia.com", "bseindia.com", "archives.nseindia.com", "nsearchives.nseindia.com", "www.screener.in",
}


class FetchError(RuntimeError):
    pass


def _throttle(host: str) -> None:
    s = get_settings()
    with _lock:
        wait = _last_hit.get(host, 0) + s.http_min_interval_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.monotonic()


def _client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(headers={"User-Agent": s.http_user_agent}, timeout=45, follow_redirects=True)


def get_text(url: str, *, cache_hours: float | None = None) -> tuple[str, bool]:
    """Return (body, from_cache). Cache keyed by URL; a stale cache is used as fallback if the source is down."""
    s = get_settings()
    cache_file = s.data_dir / "http_cache" / (hashlib.sha256(url.encode()).hexdigest() + ".html")
    ttl = (cache_hours if cache_hours is not None else s.screener_cache_hours) * 3600
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
        return cache_file.read_text(encoding="utf-8"), True
    host = urlparse(url).netloc
    _throttle(host)
    try:
        with _client() as c:
            r = c.get(url)
        if r.status_code == 404:
            raise FetchError(f"Not found: {url}")
        r.raise_for_status()
        cache_file.write_text(r.text, encoding="utf-8")
        return r.text, False
    except httpx.HTTPError as e:
        if cache_file.exists():
            log.warning("Fetch failed (%s); serving stale cache for %s", e, url)
            return cache_file.read_text(encoding="utf-8"), True
        raise FetchError(str(e)) from e


def _is_public_host(host: str) -> bool:
    import ipaddress
    import socket

    try:
        for info in socket.getaddrinfo(host, 443):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True
    except OSError:
        return False


def resolve_viewer_url(url: str) -> str:
    """Some IR sites link to an HTML viewer (…/pdf-viewer?pdf=/content/…pdf); fetch the PDF itself."""
    from urllib.parse import parse_qs, unquote, urlunparse

    u = urlparse(url)
    pdf = parse_qs(u.query).get("pdf") or parse_qs(u.query).get("file")
    if pdf and pdf[0].lower().endswith(".pdf"):
        target = unquote(pdf[0])
        if target.startswith("/"):
            return urlunparse((u.scheme, u.netloc, target, "", "", ""))
        if target.startswith("https://"):
            return target
    return url


def download_pdf(url: str, dest_dir: Path, *, company_ir_host: bool = False) -> tuple[Path, str]:
    """Download a PDF from a primary-source host. Returns (path, sha256).

    Exchange hosts are allow-listed. `company_ir_host=True` is used only for links discovered in a
    company's document listing (e.g. tcs.com investor relations) and still blocks private/loopback IPs.
    """
    s = get_settings()
    url = resolve_viewer_url(url)
    host = urlparse(url).netloc.lower()
    if urlparse(url).scheme != "https":
        raise FetchError("Only https document URLs are allowed")
    if host not in ALLOWED_DOC_HOSTS and not (company_ir_host and _is_public_host(host)):
        raise FetchError(f"Host not in primary-source allow-list: {host}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    _throttle(host)
    limit = s.max_pdf_mb * 1024 * 1024
    h = hashlib.sha256()
    tmp = dest_dir / (hashlib.sha1(url.encode()).hexdigest() + ".part")
    try:
        with _client() as c, c.stream("GET", url) as r:
            r.raise_for_status()
            # Redirect may leave the allow-list (e.g. to an unknown CDN): re-check the final host.
            final_host = r.url.host.lower()
            if final_host not in ALLOWED_DOC_HOSTS and not (company_ir_host and _is_public_host(final_host)):
                raise FetchError(f"Redirected off allow-list to {final_host}")
            size = 0
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes(65536):
                    size += len(chunk)
                    if size > limit:
                        raise FetchError(f"PDF exceeds {s.max_pdf_mb} MB limit")
                    h.update(chunk)
                    f.write(chunk)
    except httpx.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise FetchError(str(e)) from e
    except FetchError:
        tmp.unlink(missing_ok=True)
        raise
    with tmp.open("rb") as f:
        if f.read(5) != b"%PDF-":
            tmp.unlink(missing_ok=True)
            raise FetchError("Response is not a PDF")
    digest = h.hexdigest()
    final = dest_dir / f"{digest}.pdf"
    tmp.replace(final)
    return final, digest
