# Copyright (C) 2023 MikuInvidious Team
#
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
#
# MikuInvidious is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MikuInvidious. If not, see <http://www.gnu.org/licenses/>.

import ipaddress
import re
import socket
import time
from urllib.parse import urlparse

from quart import Blueprint
from shared import TicketManager, appconf
from stream import CdnConnectError, CdnProtocolError, CdnTimeoutError

dash_proxy_bp = Blueprint("dash_proxy", __name__)

DASH_CACHE_TTL = 1800
DASH_FETCH_TIMEOUT = 8.0

# Per-attempt deadline for reaching a DASH CDN URL (connect + request +
# response headers). Healthy edges answer in well under a second; a stalled
# edge must fail over to a backup URL instead of hanging. Kept at 5s (not
# lower): WARP SOCKS5 + TLS handshakes under load need headroom, and each
# "stalled" line below is this many seconds of pure zero-progress waste.
DASH_ATTEMPT_TIMEOUT = 5.0

# Mid-body watchdog for DASH track proxying. The handshake deadline above only
# covers connect + request + response headers; some edges answer headers in ms
# and then tarpit the body (~100 KB/s on a sick cache object), which used to
# hang until dash.js abandoned the request (bare 500s, no failover). Past
# DASH_BODY_GRACE seconds, a cumulative average under DASH_BODY_FLOOR_KBPS
# aborts the edge and retries the remaining Range on the next mirror.
# The floor sits well above tarpit speeds but far below healthy edge
# throughput, so sick mirrors fail over long before dash.js' fragment timeout
# fires (see player.js) instead of burning all of its retries on one edge.
DASH_BODY_GRACE = 3.0
DASH_BODY_FLOOR_KBPS = 100

# Playback fail-fast budgets. dash.js abandons slow fragments itself (~7s,
# proven live) and retries up to 5x with ABR downshift — so the proxy must
# fail a fragment fast instead of heroically churning mirrors for longer
# than the client waits (abandoned work + spins). Downloads keep the patient
# 8s/all-mirror budgets: no client intelligence there, the server must
# succeed. Caps: 2 mirrors per playback request, 4s handshakes.
DASH_PLAYBACK_ATTEMPT_TIMEOUT = 4.0
_PLAYBACK_MAX_MIRRORS = 2

# Same-mirror resume budget per playback request. When the cutting mirror
# just served bytes but every remaining mirror is range-shy at the resume
# offset (certain 416), re-requesting the remainder on the SAME mirror banks
# more bytes instead of burning the slot on a doomed one. Strictly bounded:
# each resume re-handshakes, and deeper loops outlive client patience.
_SAME_MIRROR_MAX_RESUMES = 2

# Idle-stall trip for response bodies: no bytes at all for this long means a
# wedged edge (the cumulative-average watchdog above can't see pure idleness
# — a 7s stall followed by a dribble reads as merely "slow"). Shared by
# playback and downloads; both resume byte-exact, so tripping early only
# costs a handshake.
DASH_BODY_IDLE_TIMEOUT = 5.0

# Raw CDN domains allowed through the DASH track proxy.
_ALLOWED_DASH_DOMAINS = [
    ".hdslb.com",
    ".biliimg.com",
    ".bilivideo.com",
    ".bilivideo.cn",
    ".bilibili.com",
    ".acgvideo.com",
    ".akamaized.net",
    ".mountaintoys.cn",
]




def _is_mcdn_url(url: str) -> bool:
    """Detect Bilibili M-CDN (PCDN) edge nodes (PipePipe b3303f4).

    Typically ``*.mcdn.bilivideo.cn`` / ``*.edge.mountaintoys.cn``. They are
    built for the web player: flaky HEAD, non-browser 403s, short-lived
    signatures — bad first choice for proxying/downloads.
    """
    return "mcdn.bilivideo" in url or "mountaintoys" in url or "os=mcdn" in url


def _pick_stable_dash_url(primary: str | None, backups) -> str | None:
    """Prefer a non-M-CDN URL (PipePipe ``pickStableStreamUrl``).

    Keeps the primary when it is stable; otherwise returns the first stable
    backup; falls back to the primary when everything is M-CDN.
    """
    if primary and not _is_mcdn_url(primary):
        return primary
    for cand in backups or []:
        if isinstance(cand, str) and cand and not _is_mcdn_url(cand):
            return cand
    return primary


def _is_safe_dash_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False
    if not any(hostname == d.lstrip(".") or hostname.endswith(d) for d in _ALLOWED_DASH_DOMAINS):
        return False
    return True


async def _is_safe_dash_url_async(url: str) -> bool:
    """Full DASH URL check: domain allowlist + private-IP DNS reject.

    Mirrors ``proxy.is_safe_proxy_url`` so the DASH track proxy, muxed
    downloads, and background jobs enforce the same SSRF bar as the
    progressive ``/proxy/video/`` path (L1). The sync
    :func:`_is_safe_dash_url` above stays as a fast pre-filter for
    candidate lists; call this before connecting/fetching.
    """
    if not _is_safe_dash_url(url):
        return False
    try:
        from dns_cache import resolve_host

        hostname = urlparse(url).hostname
        if not hostname:
            return False
        addr_infos = await resolve_host(hostname)
        for _, _, _, _, sockaddr in addr_infos:
            ip_obj = ipaddress.ip_address(sockaddr[0])
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_multicast
                or ip_obj.is_reserved
            ):
                return False
    except socket.gaierror:
        return False
    except Exception:
        return False
    return True


# Bilibili video IDs for Content-Disposition filenames (M2). Quart route
# params allow `"`, `;`, CR/LF-encoded chars that would break out of the
# quoted filename and inject response headers (CWE-113). Validate strictly
# and sanitize defensively at emission.
_VID_RE = re.compile(r"^(BV[a-zA-Z0-9]{10}|av\d{1,20})$")


def _is_valid_vid(vid: str) -> bool:
    return bool(vid) and bool(_VID_RE.match(vid))


def _safe_vid(vid: str) -> str:
    """Strip anything outside ``[A-Za-z0-9_-]`` for safe header embedding."""
    return "".join(c for c in (vid or "") if c.isalnum() or c in ("_", "-"))[:64]


def _safe_download_filename(vid: str, idx: int, qn: int, ext: str = ".mp4") -> str:
    try:
        idx = int(idx)
    except TypeError, ValueError:
        idx = 0
    try:
        qn = int(qn)
    except TypeError, ValueError:
        qn = 0
    ext = ext if ext in (".mp4", ".flv") else ".mp4"
    return f"{_safe_vid(vid) or 'video'}_{idx}_p{qn}{ext}"


async def _extract_ep_id(vi) -> str | None:
    """Best-effort extraction of a PGC ``ep_id`` from the video redirect URL."""
    try:
        info = await vi.get_info()
        redirect_url = info.get("redirect_url", "") or ""
        if redirect_url:
            m = re.search(r"ep(\d+)", redirect_url)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None




_FREE_DOWNLOAD_MAX_QN = 80
_DOWNLOAD_TOO_LARGE = -2

# Fallback per-track download size cap (MB) when the hoster did not configure one.
_DEFAULT_MAX_DOWNLOAD_MB = 1024


def _max_download_size_mb() -> int:
    """Hoster-configured per-track download cap in MB (``[site] max_download_size_mb``)."""
    try:
        return max(int(appconf["site"].get("max_download_size_mb", _DEFAULT_MAX_DOWNLOAD_MB)), 1)
    except TypeError, ValueError:
        return _DEFAULT_MAX_DOWNLOAD_MB


def _max_download_track_bytes() -> int:
    """Hoster-configured per-track download cap in bytes."""
    return _max_download_size_mb() * 1024 * 1024


def _download_size_status(headers: dict) -> int:
    content_length = headers.get("content-length")
    if content_length is None:
        return 0
    try:
        parsed_content_length = int(content_length)
    except ValueError:
        return -1
    if parsed_content_length < 0:
        return -1
    if parsed_content_length > _max_download_track_bytes():
        return _DOWNLOAD_TOO_LARGE
    return 0


# Sentinel returned by _download_track_to_file when a job cancel was observed.
_DOWNLOAD_CANCELLED = -3

# Resume-with-backoff when the CDN/WARP tunnel cuts a track download mid-body
# ("Upstream connection closed prematurely", connection resets, read timeouts).
# Retries are UNBOUNDED (a download never gives up unless the user cancels);
# backoff delays plateau at the last entry (16s) so a troubled download waits
# patiently instead of hammering.
_TRACK_DOWNLOAD_RETRY_DELAYS = (2.0, 4.0, 8.0, 12.0, 16.0)
_RETRYABLE_TRACK_ERRORS = (
    CdnConnectError,
    CdnProtocolError,
    CdnTimeoutError,
    TimeoutError,
    OSError,  # covers ConnectionResetError / BrokenPipeError
)


class _JobCancelled(Exception):
    """Raised when a download job is cancelled mid-flight."""


class _TrackCut(Exception):
    """Internal control flow: connection cut mid-attempt, carries partial progress."""

    def __init__(self, total: int, file_obj, cause: Exception):
        super().__init__(str(cause))
        self.total = total
        self.file_obj = file_obj
        self.cause = cause


_CUT_ERRORS = (_TrackCut, *_RETRYABLE_TRACK_ERRORS)



async def _build_dash_cdn_headers() -> dict:
    """CDN header set for DASH track requests (proxy + download).

    Bilibili's .bilivideo.com DASH CDN returns 403 when the request carries the
    Android app User-Agent (used for the API layer) — the CDN only serves DASH
    tracks to a web-browser UA. So build on the shared CDN base (web UA +
    Referer/Origin), NOT the android get_common_headers() set.
    """
    from api.client import build_cdn_headers

    headers = build_cdn_headers(referer=appconf["bili"].get("referer", "https://www.bilibili.com"))
    ticket = await TicketManager.get_ticket()
    if ticket:
        headers["x-bili-ticket"] = ticket
    headers["session_id"] = TicketManager._generate_session_id()
    headers["x-bili-trace-id"] = TicketManager._generate_trace_id()
    creds = appconf["credential"]
    if creds.get("buvid3"):
        headers["buvid"] = creds["buvid3"]
    if creds.get("buvid4"):
        headers["buvid4"] = creds["buvid4"]
    cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds.get("use_cred") else {}
    if cookie_jar:
        headers["cookie"] = "; ".join(f"{k}={v}" for k, v in cookie_jar.items())
    return headers




def _parse_content_range_start(headers) -> int | None:
    """Parse the start offset from a ``Content-Range: bytes <start>-...`` header."""
    cr = (headers or {}).get("content-range") or ""
    m = re.match(r"bytes\s+(\d+)-", cr)
    if not m:
        return None
    try:
        return int(m.group(1))
    except TypeError, ValueError:
        return None




_CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)(?:/(\d+|\*))?")
_RANGE_NOT_SATISFIABLE_RE = re.compile(r"bytes\s+\*/(\d+)")

def _shift_dash_range(orig_range: str | None, yielded: int) -> str | None:
    """Remaining Range after ``yielded`` bytes were already sent downstream.

    Returns None when nothing was sent yet (keep the original request shape).
    The byte sequence stays contiguous across mirror switches, so the retry is
    invisible to the player: response headers were already emitted from the
    first attempt.
    """
    if not yielded:
        return None
    m = re.match(r"bytes=(\d+)-(\d*)$", (orig_range or "").strip())
    if not m:
        return f"bytes={yielded}-"
    return f"bytes={int(m.group(1)) + yielded}-{m.group(2)}"


def _expected_resume_start(orig_range: str | None, yielded: int) -> int | None:
    """Expected Content-Range start after ``yielded`` bytes were sent.

    Returns None when the original request shape is unknown (failover keeps
    whatever the mirror returns).
    """
    m = re.match(r"bytes=(\d+)-(\d*)$", (orig_range or "").strip())
    if m:
        try:
            return int(m.group(1)) + yielded
        except TypeError, ValueError:
            return None
    if not orig_range:
        # Full-file (no Range) request starting at 0.
        return yielded
    return None


def _parse_content_range_full(cr: str | None) -> tuple[int | None, int | None, int | None]:
    """Parse ``Content-Range: bytes <start>-<end>/<total>`` into ints.

    Returns ``(start, end, total)`` with ``total`` None for ``*``/missing.
    Returns ``(None, None, None)`` when unparseable.
    """
    m = _CONTENT_RANGE_RE.match((cr or "").strip())
    if not m:
        return None, None, None
    try:
        start, end = int(m.group(1)), int(m.group(2))
    except TypeError, ValueError:
        return None, None, None
    total: int | None = None
    raw_total = m.group(3)
    if raw_total and raw_total != "*":
        try:
            total = int(raw_total)
        except TypeError, ValueError:
            total = None
    return start, end, total


def _parse_416_total(cr: str | None) -> int | None:
    """Parse ``Content-Range: bytes */<total>`` from a 416 response.

    Returns the total file size, or None when absent/unparseable. The
    generic ``_parse_content_range_full`` cannot handle the ``*/TOTAL``
    shape (no start-end span), so 416 handling needs its own parse.
    """
    m = _RANGE_NOT_SATISFIABLE_RE.match((cr or "").strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except TypeError, ValueError:
        return None




# after _MIRROR_SICK_COOLDOWN so recovered edges rejoin automatically.
# Single worker, so a plain dict is fine.
_MIRROR_SICK_COOLDOWN = 120.0
_MIRROR_HEALTH_MAX = 1024
_mirror_health: dict = {}  # host -> [consecutive_fails, last_fail_monotonic]


def _note_mirror_ok(host: str | None):
    """Clear a mirror's failure record after a clean full-body transfer."""
    if not host:
        return
    _mirror_health.pop(host, None)


def _note_mirror_bad(host: str | None):
    """Record a transport failure for a mirror (sick for _MIRROR_SICK_COOLDOWN)."""
    if not host:
        return
    now = time.monotonic()
    entry = _mirror_health.get(host)
    if entry is None:
        _mirror_health[host] = [1, now]
    else:
        entry[0] += 1
        entry[1] = now
    if len(_mirror_health) > _MIRROR_HEALTH_MAX:
        cutoff = now - _MIRROR_SICK_COOLDOWN
        for h in [h for h, (_, last) in _mirror_health.items() if last < cutoff]:
            del _mirror_health[h]


def _mirror_last_fail(host: str) -> float:
    entry = _mirror_health.get(host)
    return entry[1] if entry is not None else 0.0


def _parse_range_start(range_header: str | None) -> int | None:
    """Parse the start offset of a ``Range: bytes=<start>-<end?>`` header."""
    m = re.match(r"bytes=(\d+)-\d*$", (range_header or "").strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except TypeError, ValueError:
        return None


def _mirror_is_sick(host: str | None) -> bool:
    """True when a mirror failed recently and hasn't proven healthy since."""
    if not host:
        return False
    entry = _mirror_health.get(host)
    if entry is None or entry[0] <= 0:
        return False
    return time.monotonic() - entry[1] < _MIRROR_SICK_COOLDOWN


def _order_urls_by_health(urls: list) -> list:
    """Healthy mirrors first (original order), recently-failed last (oldest failure first)."""
    healthy, sick = [], []
    for u in urls:
        (sick if _mirror_is_sick(urlparse(u).hostname or "") else healthy).append(u)
    sick.sort(key=lambda u: _mirror_last_fail(urlparse(u).hostname or ""))
    return healthy + sick


# Range-shy mirror memory: a 416 whose offset is NOT past EOF means the edge
# lacks that range (thin/cold cache on a big object — observed live: one edge
# 416s deep ranges of a 124MB audio object while reporting the full total),
# and it will 416 the same offset again. Remember the shallowest rejected
# offset per host (120s cooldown, same as sick memory) so later fragments
# try capable mirrors first instead of burning a failover slot on a doomed
# one. Offset granularity matters: shallow ranges on the same host still
# serve fine, so unlike sick-marking this never removes the mirror outright.
# Single worker, so a plain dict is fine.
_MIRROR_RANGE_SHY_COOLDOWN = 120.0
_mirror_range_shy: dict = {}  # host -> [shallowest_416_offset, last_monotonic]


def _note_mirror_range_shy(host: str | None, offset: int | None):
    """Record a mid-file 416 for ``host`` at ``offset``."""
    if not host or offset is None or offset < 0:
        return
    now = time.monotonic()
    entry = _mirror_range_shy.get(host)
    if entry is None or offset < entry[0]:
        _mirror_range_shy[host] = [offset, now]
    else:
        entry[1] = now
    if len(_mirror_range_shy) > _MIRROR_HEALTH_MAX:
        cutoff = now - _MIRROR_RANGE_SHY_COOLDOWN
        for h in [h for h, (_, last) in _mirror_range_shy.items() if last < cutoff]:
            del _mirror_range_shy[h]


def _mirror_range_shy_at(host: str | None, want: int | None) -> bool:
    """True when ``host`` recently 416'd at an offset covering ``want``."""
    if not host or want is None:
        return False
    entry = _mirror_range_shy.get(host)
    if entry is None:
        return False
    if time.monotonic() - entry[1] >= _MIRROR_RANGE_SHY_COOLDOWN:
        return False
    return want >= entry[0]


def _segment_mirror_order(candidates: list, seg_index: int) -> list:
    """Per-segment mirror order: rotate healthy stable mirrors first.

    Rotation spreads back-to-back load so no single edge throttles (the
    decaying-speed failure mode); sick mirrors trail for failover only;
    M-CDN stays last-resort.
    """
    stable = [u for u in candidates if not _is_mcdn_url(u)] or list(candidates)
    tail = [u for u in candidates if u not in stable]
    healthy = [u for u in stable if not _mirror_is_sick(urlparse(u).hostname or "")]
    sick = [u for u in stable if u not in healthy]
    if not healthy:
        return sick + tail
    rot = seg_index % len(healthy)
    return healthy[rot:] + healthy[:rot] + sick + tail




_DOWNLOAD_PACE_DELAYS = (2.0, 4.0, 6.0, 8.0, 10.0)
_DOWNLOAD_GOOD_BPS = 1024 * 1024


def _pace_delay(slow_streak: int) -> float:
    """Pause for the current consecutive-slow-trip count (0 = base inter-segment gap)."""
    return _DOWNLOAD_PACE_DELAYS[min(max(slow_streak, 0), len(_DOWNLOAD_PACE_DELAYS) - 1)]


# Current-speed slowdown trip: fixed 1s windows; 3 consecutive windows under
# _SLOW_WINDOW_KBPS treats the connection like a premature disconnect and
# fails over. Averages react too slowly (a collapsed edge hides behind its own
# fast start for minutes) and burst-set peaks trip healthy-hundreds speeds —
# the current window is always the truth about right now. Three strikes (not
# one) so an isolated bad second on a bursty-but-healthy edge doesn't earn a
# 10s pause. Armed only after capability is proven (a >= _SLOW_ARM_BPS window
# seen this track — uniformly slow links never trip; the absolute watchdog
# still guards true stalls), and bounded to one probe per mirror per segment,
# then patience.
_SLOW_WINDOW = 1.0
_SLOW_WINDOW_KBPS = 300
_SLOW_ARM_BPS = 1024 * 1024
_SLOW_WINDOW_STRIKES = 3
# Pause after a slow trip before reconnecting: the edge is throttling, and a
# fresh hammer lands back in the penalty box. Fixed 10s (not escalating —
# escalation lives in the inter-segment pacing ladder).
_SLOW_TRIP_PAUSE = 10.0



