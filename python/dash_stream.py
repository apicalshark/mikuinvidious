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

import asyncio
import time
from urllib.parse import urlparse

from dash_common import (
    _CONTENT_RANGE_RE,
    _PLAYBACK_MAX_MIRRORS,
    _SAME_MIRROR_MAX_RESUMES,
    DASH_ATTEMPT_TIMEOUT,
    DASH_BODY_FLOOR_KBPS,
    DASH_BODY_GRACE,
    DASH_BODY_IDLE_TIMEOUT,
    DASH_PLAYBACK_ATTEMPT_TIMEOUT,
    _build_dash_cdn_headers,
    _expected_resume_start,
    _is_mcdn_url,
    _is_safe_dash_url,
    _is_safe_dash_url_async,
    _mirror_range_shy_at,
    _note_mirror_bad,
    _note_mirror_ok,
    _note_mirror_range_shy,
    _order_urls_by_health,
    _parse_416_total,
    _parse_content_range_full,
    _parse_range_start,
    _pick_stable_dash_url,
    _shift_dash_range,
    dash_proxy_bp,
)
from dash_fetch import (
    _load_dash_data,
    _lookup_track,
    _playurl_url_expiry,
    _refresh_dash_data,
    has_valid_dash_tracks,
)
from quart import Response, request
from rate_limit import get_client_ip, rate_limit
from shared import UPSTREAM_RESPONSE_BLACKLIST, Network, TicketManager, appconf
from stream import CdnConnectError, CdnConnection, CdnProtocolError, CdnTimeoutError


def _dash_candidate_urls(track: dict) -> list:
    """Primary + backup CDN URLs for a DASH track, safety-checked.

    Bilibili serves every track from ``base_url`` with 2-3 mirrors in
    ``backup_url``. The primary edge sometimes stalls individual objects
    (cold cache / tarpit: latency swinging from ms to 25s+), so the proxy
    must be able to fail over instead of hanging on the primary.

    M-CDN (PCDN) edges are deprioritized but kept as last-resort mirrors
    (PipePipe b3303f4 ``pickStableStreamUrl``): the stable URL goes first,
    remaining mirrors follow for failover.
    """
    candidates = []
    primary = track.get("base_url") or track.get("baseUrl")
    backups = track.get("backup_url") or track.get("backupUrl") or []
    if isinstance(backups, str):
        backups = [backups]
    if not isinstance(backups, list):
        backups = []
    ordered = []
    stable = _pick_stable_dash_url(primary, backups)
    if stable:
        ordered.append(stable)
    for url in [primary, *backups]:
        if isinstance(url, str) and url and url not in ordered:
            ordered.append(url)
    # Stable (non-M-CDN) first, M-CDN last as failover-only.
    ordered.sort(key=lambda u: 1 if _is_mcdn_url(u) else 0)
    for url in ordered:
        if isinstance(url, str) and url and url not in candidates and _is_safe_dash_url(url):
            candidates.append(url)
    return candidates


async def _fetch_dash_attempt(url: str, headers: dict, proxy_url: str, timeout: float = DASH_ATTEMPT_TIMEOUT):
    """Connect + request + read headers for one candidate URL.

    Bounded by ``timeout`` so a stalled edge fails fast and
    the caller can try the next mirror. Returns ``(conn, resp_headers)``;
    the caller owns ``conn.close()``. Raises ``TimeoutError`` on stall
    (closed connection included) and propagates other errors.
    """
    conn = CdnConnection(url, headers=headers, proxy_url=proxy_url)
    try:
        async with asyncio.timeout(timeout):
            resp_headers = await _dash_handshake(conn)
        return conn, resp_headers
    except BaseException:
        await conn.close()
        raise


async def _dash_handshake(conn: CdnConnection):
    """One connect + request + header-read sequence (deadline applied by caller)."""
    await conn.connect()
    await conn.send_request()
    return await conn.read_response_headers()


async def _refresh_dash_ticket(headers: dict) -> dict:
    """Refresh the bili ticket after a 403-class response. Returns updated headers."""
    headers = dict(headers)
    ticket = await TicketManager.get_ticket(force_refresh=True)
    if ticket:
        headers["x-bili-ticket"] = ticket
    else:
        headers.pop("x-bili-ticket", None)
    headers["session_id"] = TicketManager._generate_session_id()
    headers["x-bili-trace-id"] = TicketManager._generate_trace_id()
    return headers


class _DashUpstreamError(Exception):
    """All candidate mirrors failed for a DASH track.

    ``forbidden`` is True when at least one mirror answered 403/412/514
    (signature/risk-control rejection — the URL set itself is likely dead,
    not just a sick edge, so callers may try a fresh playurl instead of
    merely another mirror).
    """

    def __init__(self, message="", *, forbidden=False):
        super().__init__(message)
        self.forbidden = forbidden




def _dash_response_lengths(resp_headers) -> tuple[str | None, str | None]:
    """Derive guaranteed ``(Content-Length, Content-Range)`` for a track response.

    dash.js measures throughput and drives its ABR/abandon decisions from XHR
    progress events. When the response carries no computable length
    (``lengthComputable`` false), ``bytesTotal`` stays NaN, the
    abandon-requests rule can never trigger, and a slow fragment hangs until
    the XHR timeout fires as "Request timeout: non-computable download size"
    (dash.js#4716) with ABR stuck on the top rendition.

    Returns ``(content_length, content_range)`` with ``content_range`` None
    for ``200`` responses. Returns ``(None, None)`` when a ``200``/``206``
    response cannot provide computable lengths (missing/invalid headers) —
    the caller must treat that mirror as unusable and try the next one
    instead of serving a lengthless stream. Error statuses also return
    ``(None, None)`` (lengths irrelevant there).
    """
    status = resp_headers.status_code
    if status not in (200, 206):
        return None, None
    headers = resp_headers.headers or {}

    cl: int | None = None
    cl_raw = headers.get("content-length")
    if cl_raw is not None:
        try:
            cl = int(str(cl_raw).strip())
        except TypeError, ValueError:
            cl = None
        if cl is not None and cl < 0:
            cl = None

    content_range: str | None = None
    if status == 206:
        cr_raw = (headers.get("content-range") or "").strip()
        m = _CONTENT_RANGE_RE.match(cr_raw)
        if not m:
            return None, None
        start, end = int(m.group(1)), int(m.group(2))
        if end < start:
            return None, None
        total = m.group(3) or "*"
        content_range = f"bytes {start}-{end}/{total}"
        # A 206 length must equal the range span; prefer the range-derived
        # value so a mismatched/odd upstream Content-Length cannot desync
        # the byte count dash.js expects.
        cl = end - start + 1

    if cl is None:
        return None, None
    return str(cl), content_range


class _SlowDashBody(Exception):
    """Upstream body trickling below the watchdog floor (retryable on next mirror)."""


async def _yield_dash_body(conn, label: str, floor_watchdog: bool = True):
    """Yield upstream body chunks with a throughput watchdog.

    Past ``DASH_BODY_GRACE`` seconds, a cumulative average under
    ``DASH_BODY_FLOOR_KBPS`` raises ``_SlowDashBody`` so the caller retries the
    remaining Range on the next mirror instead of tarpitting until dash.js
    abandons the request. Small (sidx/init) responses finish inside the grace
    period and never trip.

    Separately, no bytes at all for ``DASH_BODY_IDLE_TIMEOUT`` raises too: a
    wedged edge that idles (then dribbles) is invisible to the cumulative
    average until far too late.

    ``floor_watchdog=False`` disables the speed trip (keeps the idle trip).
    Slow is not failure: like Bilibili's own player (backup URLs only on
    failure), the playback proxy must not switch mirrors merely because an
    edge is slow — dash.js owns slowness (abandon + ABR). Speed trips stay on
    for downloads, where no client exists to catch them.
    """
    got = 0
    t0 = time.monotonic()
    resumed = isinstance(conn, _ResumedDashConn)
    raw_conn = conn._conn if resumed else conn
    iterator = raw_conn.iter_chunks()
    while True:
        try:
            async with asyncio.timeout(DASH_BODY_IDLE_TIMEOUT):
                chunk = await iterator.__anext__()
        except StopAsyncIteration:
            return
        except TimeoutError as e:
            raise _SlowDashBody(f"no data for {DASH_BODY_IDLE_TIMEOUT:.0f}s (idle stall)") from e
        got += len(chunk)
        el = time.monotonic() - t0
        if floor_watchdog and el > DASH_BODY_GRACE and got / el < DASH_BODY_FLOOR_KBPS * 1024:
            raise _SlowDashBody(f"{got / el / 1024:.0f} KB/s < {DASH_BODY_FLOOR_KBPS} KB/s after {el:.1f}s")
        if resumed:
            if conn._skip > 0:
                if len(chunk) <= conn._skip:
                    conn._skip -= len(chunk)
                    continue
                chunk = chunk[conn._skip :]
                conn._skip = 0
            if conn._limit is not None:
                if conn._limit <= 0:
                    return
                if len(chunk) > conn._limit:
                    chunk = chunk[: conn._limit]
                conn._limit -= len(chunk)
                yield chunk
                if conn._limit <= 0:
                    return
                continue
        yield chunk




class _ResumedDashConn:
    """Wrap a mirror that did not resume contiguously (Range ignored / offset).

    Skips ``skip`` prefix bytes of the upstream body and caps output at
    ``limit`` bytes so the byte sequence stays contiguous with what was
    already sent downstream (downstream headers were emitted from the first
    mirror and cannot change). ``limit`` None means unbounded.
    """

    def __init__(self, conn, skip: int, limit: int | None):
        self._conn = conn
        self._skip = max(int(skip or 0), 0)
        self._limit = limit

    async def iter_chunks(self):
        skip = self._skip
        limit = self._limit
        async for chunk in self._conn.iter_chunks():
            if skip > 0:
                if len(chunk) <= skip:
                    skip -= len(chunk)
                    continue
                chunk = chunk[skip:]
                skip = 0
            if limit is not None:
                if limit <= 0:
                    break
                if len(chunk) > limit:
                    chunk = chunk[:limit]
                limit -= len(chunk)
                yield chunk
                if limit <= 0:
                    break
            else:
                yield chunk

    async def close(self):
        try:
            await self._conn.close()
        except Exception:
            pass


async def _failover_dash_conn(
    pending: list,
    headers: dict,
    proxy_url: str,
    label: str,
    orig_range,
    yielded: int,
    downstream_total: int | None = None,
    attempt_timeout: float | None = None,
    retry_url: str | None = None,
    retry_served: int = 0,
    retry_budget: int = 0,
):
    """Fail over a cut/slow body to the next mirror.

    Shifts ``headers["range"]`` when bytes were already sent, then walks
    ``pending`` until a mirror answers with a byte-contiguous resume point.
    Mirrors that answer 206 at the expected offset are used directly.
    Mirrors that ignore the resume Range (200 full body) or answer 206 at an
    earlier offset that still covers the resume point are salvaged by
    skipping the already-sent prefix (capped to the downstream remainder),
    because re-requesting cannot change bytes already flushed to the player.
    Mirrors that start past the resume point (gap) or whose body is shorter
    than the resume point are skipped. Removes the used URL from ``pending`` and
    returns ``(conn, used_url, resp_headers)``; raises ``_DashUpstreamError``
    when no mirror is usable. The caller owns ``conn.close()`` (wrapper or raw).

    ``retry_url`` is the just-cut mirror: when it served bytes right up to
    the cut (``retry_served > 0``) but every remaining candidate is
    range-shy at the resume offset (certain 416), it is appended as a
    last-before-doomed candidate — proven partial progress beats certain
    failure. Bounded by the caller's ``retry_budget`` (each use consumes one;
    the caller tracks it by comparing the returned URL).
    """
    if yielded:
        headers["range"] = _shift_dash_range(orig_range, yielded) or headers.get("range")
    expected = _expected_resume_start(orig_range, yielded) if yielded else None
    remaining = downstream_total - yielded if downstream_total is not None and yielded else None
    if remaining is not None and remaining < 0:
        remaining = 0
    orig_pending = list(pending)
    candidates = list(pending)
    can_retry_same = (
        bool(retry_url)
        and retry_budget > 0
        and retry_served > 0
        and expected is not None
        and orig_pending
        and retry_url not in candidates
    )
    retry_added = False
    if can_retry_same and all(_mirror_range_shy_at(urlparse(u).hostname or "", expected) for u in orig_pending):
        # Every remaining mirror will 416 this offset; the cutting mirror
        # just proved it can serve bytes. Health ordering inside
        # _fetch_dash_track keeps capable mirrors first anyway — this only
        # outranks the doomed ones via the range-shy partition.
        candidates.append(retry_url)
        retry_added = True
    last_error: Exception | None = None
    forbidden_seen = False

    def _take(used: str):
        candidates.remove(used)
        if used in pending:
            pending.remove(used)

    while candidates:
        try:
            conn, resp_headers, used, _, _ = await _fetch_dash_track(
                candidates, headers, proxy_url, label, attempt_timeout=attempt_timeout
            )
        except _DashUpstreamError as exc:
            if (
                can_retry_same
                and not retry_added
                and all(_mirror_range_shy_at(urlparse(u).hostname or "", expected) for u in orig_pending)
            ):
                candidates = [retry_url]
                retry_added = True
                continue
            raise exc
        if resp_headers.status_code in (403, 412, 514):
            # Auth-level rejection (expired signature): remember it for the
            # caller's refresh decision even if later failures differ.
            forbidden_seen = True
        if yielded:
            status = resp_headers.status_code
            if status == 206:
                start, end, _ = _parse_content_range_full(resp_headers.headers.get("content-range"))
                if expected is None or start == expected:
                    _take(used)
                    return conn, used, resp_headers
                if start is not None and end is not None and expected is not None and start < expected <= end:
                    skip = expected - start
                    _take(used)
                    print(
                        f"[DashProxy] {label} {urlparse(used).hostname} resume overlap "
                        f"(got {start}-{end}, want {expected}, skipping {skip}), salvaging"
                    )
                    return _ResumedDashConn(conn, skip, remaining), used, resp_headers
                print(
                    f"[DashProxy] {label} {urlparse(used).hostname} resume mismatch "
                    f"(want start={expected}, got status={status} "
                    f"range={resp_headers.headers.get('content-range')}), trying next mirror"
                )
                _take(used)
                try:
                    await conn.close()
                except Exception:
                    pass
                last_error = RuntimeError(f"resume offset mismatch on {used}")
                continue
            if status == 416:
                # Range Not Satisfiable: the mirror tells us its file size via
                # ``Content-Range: bytes */<total>``. When our resume offset is
                # already past EOF, no mirror can satisfy it — byte accounting
                # drifted (e.g. salvaged-200 prefix math across mirrors with
                # divergent lengths). Abort immediately instead of burning
                # through every mirror: the caller truncates and the player
                # re-requests. Deliberately not forbidden (a fresh playurl
                # with the same bad offset 416s again) and not sick-marked
                # (shallow ranges on this host still serve) — instead the
                # offset is recorded in range-shy memory so later fragments
                # skip this mirror for deep ranges instead of burning a
                # failover slot on a repeat 416.
                total = _parse_416_total(resp_headers.headers.get("content-range"))
                _take(used)
                try:
                    await conn.close()
                except Exception:
                    pass
                if expected is not None and total is not None and expected >= total:
                    raise _DashUpstreamError(
                        f"resume offset {expected} past EOF (total {total}) for {label}",
                    )
                if expected is not None:
                    _note_mirror_range_shy(urlparse(used).hostname or "", expected)
                print(
                    f"[DashProxy] {label} {urlparse(used).hostname} range rejected "
                    f"(want start={expected}, total={total}), trying next mirror"
                )
                last_error = RuntimeError(f"range rejected (416) on {used}")
                continue
            if status == 200 and expected is not None:
                # Server ignored Range: full body from 0, discard the prefix.
                try:
                    total = int(str(resp_headers.headers.get("content-length") or "").strip())
                except TypeError, ValueError:
                    total = None
                if total is not None and total > expected:
                    limit = total - expected if remaining is None else min(remaining, total - expected)
                    _take(used)
                    print(
                        f"[DashProxy] {label} {urlparse(used).hostname} ignored resume Range "
                        f"(200 full body, discarding {expected} prefix), salvaging"
                    )
                    return _ResumedDashConn(conn, expected, limit), used, resp_headers
                print(
                    f"[DashProxy] {label} {urlparse(used).hostname} resume mismatch "
                    f"(want start={expected}, got status={status} "
                    f"range={resp_headers.headers.get('content-range')}), trying next mirror"
                )
                _take(used)
                try:
                    await conn.close()
                except Exception:
                    pass
                last_error = RuntimeError(f"resume offset mismatch on {used}")
                continue
            print(
                f"[DashProxy] {label} {urlparse(used).hostname} resume mismatch "
                f"(want start={expected}, got status={resp_headers.status_code} "
                f"range={resp_headers.headers.get('content-range')}), trying next mirror"
            )
            _take(used)
            try:
                await conn.close()
            except Exception:
                pass
            last_error = RuntimeError(f"resume offset mismatch on {used}")
            continue
        _take(used)
        return conn, used, resp_headers
    raise _DashUpstreamError(
        f"all mirrors failed for {label}: {last_error}",
        forbidden=forbidden_seen or getattr(last_error, "forbidden", False),
    )


# Cross-request sick-mirror memory. Per-request failover always restarts at
# the primary, so one sick edge poisons every fragment (each burns seconds
# failing over) while the player sees only slowness. These marks make the
# *next* request start on a healthy mirror instead.
#
# Only transport health is tracked: handshake stalls, connection/protocol
# errors, mid-body cuts, slow bodies, unusable responses. 403/412/514 are
# auth-level (dead signatures — handled by the ticket/playurl refresh paths)
# and never mark a mirror sick. Success clears immediately; failures expire
# after _MIRROR_SICK_COOLDOWN so recovered edges rejoin automatically.


async def _fetch_dash_track(
    urls: list, headers: dict, proxy_url: str, label: str, attempt_timeout: float | None = None
):
    """Try each candidate mirror in order; return ``(conn, resp_headers, used_url, content_length, content_range)``.
    Stalled edges fail fast (``DASH_ATTEMPT_TIMEOUT``) so playback falls over
    to the next mirror instead of hanging until dash.js abandons the request
    (which used to surface as a bare 500 with no app-side log). Mirrors whose
    ``200``/``206`` response cannot provide computable lengths (see
    ``_dash_response_lengths``) are likewise skipped: serving them would leave
    dash.js with non-computable progress events, breaking throughput/ABR and
    ending in "Request timeout: non-computable download size".     Raises
    ``_DashUpstreamError`` when every mirror fails; the caller owns
    ``conn.close()`` on success.

    Recently-failed mirrors are tried last (see sick-mirror memory): every
    fragment otherwise restarts at a sick primary and burns seconds before
    failing over.
    """
    ticket_refreshed = False
    last_error: Exception | None = None
    forbidden_seen = False
    handshake_timeout = attempt_timeout or DASH_ATTEMPT_TIMEOUT
    urls = _order_urls_by_health(urls)
    want = _parse_range_start(headers.get("range"))
    if want is not None:
        # Range-shy mirrors (416'd this offset before) go last: they will
        # 416 again instead of serving. Capable mirrors first.
        urls = [u for u in urls if not _mirror_range_shy_at(urlparse(u).hostname or "", want)] + [
            u for u in urls if _mirror_range_shy_at(urlparse(u).hostname or "", want)
        ]
    for url in urls:
        host = urlparse(url).hostname or url
        try:
            conn, resp_headers = await _fetch_dash_attempt(url, headers, proxy_url, timeout=handshake_timeout)
        except TimeoutError as exc:
            last_error = exc if str(exc) else TimeoutError(f"{host} stalled")
            _note_mirror_bad(urlparse(url).hostname or "")
            print(f"[DashProxy] {label} {host} stalled, trying next mirror")
            continue
        except (CdnConnectError, CdnProtocolError, CdnTimeoutError, OSError) as exc:
            last_error = exc
            _note_mirror_bad(urlparse(url).hostname or "")
            print(f"[DashProxy] {label} {host} failed ({exc}), trying next mirror")
            continue
        if resp_headers.status_code in [403, 412, 514]:
            await conn.close()
            forbidden_seen = True
            last_error = RuntimeError(f"CDN returned {resp_headers.status_code}")
            if not ticket_refreshed:
                headers = await _refresh_dash_ticket(headers)
                ticket_refreshed = True
            print(f"[DashProxy] {label} {host} -> {resp_headers.status_code}, trying next mirror")
            continue
        if resp_headers.status_code == 416:
            total = _parse_416_total(resp_headers.headers.get("content-range"))
            if want is not None and total is not None and want >= total:
                # Genuinely past EOF: the 416 is the correct signal, forward it.
                return conn, resp_headers, url, None, None
            # Mid-file rejection: this mirror lacks the range (thin edge
            # cache) and will 416 it again — remember and try the next one.
            # Deliberately not sick-marked (shallow ranges still serve).
            if want is not None:
                _note_mirror_range_shy(urlparse(url).hostname or "", want)
                print(f"[DashProxy] {label} {host} lacks range at {want} (416, total={total}), trying next mirror")
            else:
                print(f"[DashProxy] {label} {host} -> 416, trying next mirror")
            try:
                await conn.close()
            except Exception:
                pass
            last_error = RuntimeError(f"CDN returned 416 for {url}")
            continue
        if resp_headers.status_code in (200, 206):
            content_length, content_range = _dash_response_lengths(resp_headers)
            if content_length is None:
                await conn.close()
                _note_mirror_bad(urlparse(url).hostname or "")
                last_error = RuntimeError("CDN response has no computable length")
                print(f"[DashProxy] {label} {host} has no computable length, trying next mirror")
                continue
        else:
            content_length, content_range = None, None
        return conn, resp_headers, url, content_length, content_range
    raise _DashUpstreamError(
        f"all mirrors failed for {label}: {last_error}",
        forbidden=forbidden_seen,
    )


async def _retry_proxy_with_fresh_playurl(
    vid, idx, media_type, qn, cid, old_urls, headers, proxy_url, label, attempt_timeout=None
):
    """One fresh-playurl retry for a 403-swept track (official player recovery).

    Drops the cached playurl (whose signatures likely expired — all mirrors
    of one response share the expiry window), re-fetches, and retries the
    request on the new mirror set. Returns ``((conn, resp_headers, used_url,
    content_length, content_range), urls)`` or None when there is nothing
    usable to retry with. The caller owns ``conn.close()`` on success.

    Never loops: at most one refresh per call, and a refresh that yields the
    same expiry stamp as the dead set (the player's same-stamp dedup) is
    declined — retrying identical signatures cannot succeed.
    """
    fresh_data = await _refresh_dash_data(vid, idx)
    if not has_valid_dash_tracks(fresh_data):
        return None
    track = _lookup_track(fresh_data, media_type, qn, cid)
    if not track:
        return None
    new_urls = _dash_candidate_urls(track)
    verdicts = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in new_urls))
    new_urls = [u for u, ok in zip(new_urls, verdicts, strict=False) if ok]
    if not new_urls:
        return None
    old_stamp = _playurl_url_expiry(old_urls[0]) if old_urls else None
    new_stamp = _playurl_url_expiry(new_urls[0])
    if old_stamp is not None and new_stamp == old_stamp:
        print(f"[DashProxy] {label} refresh returned same URL stamp ({new_stamp}), not retrying")
        return None
    print(f"[DashProxy] {label} refreshed playurl (stamp {old_stamp} -> {new_stamp}), retrying request")
    try:
        fetched = await _fetch_dash_track(new_urls, headers, proxy_url, label, attempt_timeout=attempt_timeout)
    except _DashUpstreamError as exc:
        print(f"[DashProxy] {label} fresh playurl also failed: {exc}")
        return None
    return fetched, new_urls


# Player-style segmented download: fetch the track the way dash.js does.
#
# The player never GETs a whole track: it issues small Range requests
# (sidx/init/media fragments, a few MB each) through ``proxy_dash``, each with
# its own handshake deadline, mirror failover, and throughput watchdog, plus
# up to 5 retries per fragment and ABR abandon of slow ones. The old
# downloader did the opposite — one giant whole-file GET with no speed
# watchdog, so a tarpit edge dribbling ~100 KB/s never tripped the 30s *idle*
# read timeout and the job looked "stuck" for tens of minutes while the player
# sailed past the same edge. This fetches sequential fixed-size segments with
# the exact same machinery (``_failover_dash_conn`` + ``_yield_dash_body`` +
# ``_ResumedDashConn``), writing them to ``dest`` instead of the HTTP
# response, so a sick edge costs one segment — not the file.
_DOWNLOAD_SEGMENT_SIZE = 32 * 1024 * 1024

# Disk writes are batched to this size: per-64KB-chunk asyncio.to_thread hops
# cost ~800 threadpool round-trips/sec at speed for zero benefit.
_DOWNLOAD_WRITE_BATCH = 1024 * 1024

# Adaptive pacing ladder for downloads: dash.js fragment requests arrive seconds
# apart (buffer-driven), while this loop fires back-to-back at full speed —
# sustained hammering earns shed load (empty closes) and escalating per-IP
# throttling that reads as decaying speed (fast start, then clamp). Pauses let
# the punishment decay: each consecutive slow trip steps down the ladder
# (4/6/9/12/14s, holding 14s); clean fast segments reset to the base.


async def _dash_ip_key(req) -> str:
    """Coarse per-IP bucket across all DASH fragment requests (abuse guard)."""
    return f"{get_client_ip(req)}:dash"


async def _dash_frag_key(req) -> str:
    """Per-fragment rate-limit bucket: path + Range header.

    Every fragment of a rendition shares one route path (the byte range rides
    in a header), so the default ``{ip}:{path}`` key lumps the whole stream
    into a single bucket — seeks, 2x playback, and abandon re-requests then
    429 legitimate burst traffic and dash.js stalls on our own 429s. Keying on
    the requested Range gives each fragment its own bucket while identical
    retries still share one.
    """
    rng = (req.headers.get("Range") or req.headers.get("range") or "").strip()
    return f"{get_client_ip(req)}:{req.path}:{rng}"


@dash_proxy_bp.route("/proxy/dash/<vid>/<int:idx>/<media_type>/<int:qn>/<int:cid>")
@rate_limit(limit=600, window=60, key_func=_dash_ip_key)
@rate_limit(limit=100, window=60, key_func=_dash_frag_key)
async def proxy_dash(vid, idx, media_type, qn, cid):
    """Range-capable DASH track proxy through the WARP SOCKS5 tunnel.

    Resolves the track URL from the cached dash JSON, validates it, and proxies
    the client's Range request upstream, faithfully emitting ``206 Partial
    Content`` with ``Content-Range``/``Content-Length``/``ETag`` so the sidx
    (SegmentBase indexRange) can drive byte-range seeking in dash.js.

    Each track is tried on at most two mirrors (healthy-first): dash.js
    abandons slow fragments itself and retries with ABR downshift, so deeper
    per-request sweeps only outlive client patience. Individual objects
    sometimes stall on one edge while siblings serve in milliseconds — the
    sick-mirror memory routes the next fragment straight to a healthy one.
    """
    started = time.monotonic()
    host = "-"
    conn = None
    if media_type not in ("video", "audio"):
        return Response("Bad Request: media_type must be video|audio", status=400)

    try:
        dash_data = await _load_dash_data(vid, idx)
        if not dash_data:
            return Response("Not Found", status=404)
        track = _lookup_track(dash_data, media_type, qn, cid)
        if not track:
            return Response("Not Found", status=404)

        urls = _dash_candidate_urls(track)
        if not urls:
            return Response("Not Found: track has no URL", status=404)
        # Full SSRF check (allowlist + private-IP DNS reject, same bar as
        # /proxy/video/) before touching any candidate edge. Checked
        # concurrently: sequential awaits cost one DNS RTT per candidate.
        verdicts = await asyncio.gather(*(_is_safe_dash_url_async(u) for u in urls))
        urls = [u for u, ok in zip(urls, verdicts, strict=False) if ok]
        if not urls:
            return Response("Forbidden: Invalid proxy target", status=403)
        # Fail fast, not heroically: dash.js abandons slow fragments itself
        # (~7s, with 5 retries + ABR downshift), so churning more than two
        # mirrors per request only guarantees the client gives up first and
        # the work is wasted. Sick-mirror memory (health ordering) makes
        # those two attempts count.
        urls = _order_urls_by_health(urls)[:_PLAYBACK_MAX_MIRRORS]
        if not urls:
            return Response("Forbidden: Invalid proxy target", status=403)

        creds = appconf["credential"]
        cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds.get("use_cred") else {}

        headers = await _build_dash_cdn_headers()

        # Forward runtime Range/conditional headers from the browser player
        for k, v in request.headers.items():
            if k.lower() in ["range", "if-range", "x-playback-session-id", "if-modified-since", "if-none-match"]:
                headers[k.lower()] = v

        if cookie_jar:
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_jar.items())
            existing = headers.get("cookie", "")
            headers["cookie"] = f"{existing}; {cookie_str}".lstrip("; ") if existing else cookie_str

        proxy_url = Network.get_proxy()
        label = f"{vid}:{idx} {media_type}/{qn}/{cid}"
        try:
            conn, resp_headers, used_url, content_length, content_range = await _fetch_dash_track(
                urls, headers, proxy_url, label, attempt_timeout=DASH_PLAYBACK_ATTEMPT_TIMEOUT
            )
        except _DashUpstreamError as exc:
            old_stamp = _playurl_url_expiry(urls[0]) if urls else None
            provably_expired = old_stamp is not None and time.time() > old_stamp
            if not exc.forbidden and not provably_expired:
                print(f"[DashProxy] proxy_dash error: {exc}")
                return Response("Upstream error", status=502)
            # Every mirror rejected the signature (expired playurl): fetch a
            # fresh playurl once and retry, like the official player's
            # backup-URL-then-prefetchPlayUrl recovery. Gated on 403-class or
            # provably-past signatures so ordinary dead videos and slow edges
            # don't amplify upstream playurl calls.
            reason = "forbidden" if exc.forbidden else "expired"
            print(f"[DashProxy] {label} URLs {reason} ({exc}), refreshing playurl once")
            retry = await _retry_proxy_with_fresh_playurl(
                vid,
                idx,
                media_type,
                qn,
                cid,
                urls,
                headers,
                proxy_url,
                label,
                attempt_timeout=DASH_PLAYBACK_ATTEMPT_TIMEOUT,
            )
            if retry is None:
                return Response("Upstream error", status=502)
            (conn, resp_headers, used_url, content_length, content_range), urls = retry

        host = urlparse(used_url).hostname or "-"
        pending = [u for u in urls if u != used_url]
        orig_range = headers.get("range")
        yielded = 0
        try:
            downstream_total = int(str(content_length).strip()) if content_length is not None else None
        except TypeError, ValueError:
            downstream_total = None

        async def generate():
            nonlocal conn, host, yielded, used_url
            same_resumes = 0
            while True:
                attempt_base = yielded
                try:
                    # Floor watchdog stays on here: genuine tarpits (<100KB/s
                    # past grace) fail over to the next mirror transparently.
                    # dash.js owns *mild* slowness (abandon + ABR), but it
                    # cannot cover two silent stalls (no player logs): a big
                    # top-quality buffer suppresses abandon urgency, and
                    # pinned audio (ABR off) has no downshift at all. Idle
                    # wedges still trip.
                    async for chunk in _yield_dash_body(conn, label, floor_watchdog=True):
                        yielded += len(chunk)
                        yield chunk
                except asyncio.CancelledError, GeneratorExit:
                    # Client went away mid-stream: just close upstream.
                    try:
                        await conn.close()
                    except Exception:
                        pass
                    print(f"[DashProxy] {label} {host} client disconnected after {yielded} bytes")
                    raise
                except (
                    _SlowDashBody,
                    CdnProtocolError,
                    CdnTimeoutError,
                    CdnConnectError,
                    OSError,
                    TimeoutError,
                ) as exc:
                    try:
                        await conn.close()
                    except Exception:
                        pass
                    _note_mirror_bad(host)
                    kind = "body too slow" if isinstance(exc, _SlowDashBody) else "mid-body cut"
                    if not pending:
                        # No mirrors left: end the stream early WITHOUT
                        # raising. Downstream Content-Length was already
                        # emitted, so the short body surfaces as a length
                        # mismatch and dash.js retries the Range — instead
                        # of a 500 + TaskGroup traceback from re-raising
                        # inside the generator.
                        print(
                            f"[DashProxy] {label} {host} {kind} after {yielded} bytes ({exc}); mirrors exhausted, truncating"
                        )
                        return
                    print(f"[DashProxy] {label} {host} {kind} after {yielded} bytes ({exc}), trying next mirror")
                    prev_url = used_url
                    served = yielded - attempt_base
                    try:
                        conn, used, _ = await _failover_dash_conn(
                            pending,
                            headers,
                            proxy_url,
                            label,
                            orig_range,
                            yielded,
                            downstream_total,
                            attempt_timeout=DASH_PLAYBACK_ATTEMPT_TIMEOUT,
                            retry_url=prev_url,
                            retry_served=served,
                            retry_budget=_SAME_MIRROR_MAX_RESUMES - same_resumes,
                        )
                    except _DashUpstreamError as exc2:
                        print(f"[DashProxy] {label} body failover failed: {exc2}")
                        return
                    if used == prev_url:
                        same_resumes += 1
                    used_url = used
                    host = urlparse(used).hostname or "-"
                    continue
                try:
                    await conn.close()
                except Exception:
                    pass
                _note_mirror_ok(host)
                return

        proxy_resp = Response(generate(), status=resp_headers.status_code)
        proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
        proxy_resp.headers["X-Accel-Buffering"] = "no"
        proxy_resp.headers["Accept-Ranges"] = "bytes"

        for k, v in resp_headers.headers.items():
            if k.lower() in UPSTREAM_RESPONSE_BLACKLIST:
                continue
            if k in [
                "content-type",
                "etag",
                "last-modified",
                "cache-control",
            ]:
                proxy_resp.headers[k] = v

        # Computable lengths are load-bearing for dash.js (throughput, ABR,
        # abandon rule): always emit the validated/derived values instead of
        # blindly forwarding upstream, which may omit or mismatch them.
        if content_length is not None:
            proxy_resp.headers["Content-Length"] = content_length
        if content_range is not None:
            proxy_resp.headers["Content-Range"] = content_range

        # Segments are immutable for a given track+Range, so let the browser
        # cache them aggressively: ABR flapping between renditions then
        # re-requests identical ranges, which become instant cache hits
        # instead of fresh trips over a shaky uplink.
        proxy_resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"

        if resp_headers.status_code in [200, 206]:
            current_ct = proxy_resp.headers.get("Content-Type", "").lower()
            if not current_ct or "application/octet-stream" in current_ct:
                proxy_resp.headers["Content-Type"] = "video/mp4" if media_type == "video" else "audio/mp4"
        return proxy_resp
    except asyncio.CancelledError:
        # Client (dash.js/Caddy) abandoned a hung upstream wait. Re-raise so
        # Granian handles the disconnect, but leave a trace: these used to be
        # the mysterious log-free 500s.
        print(f"[DashProxy] {media_type}/{qn}/{cid} {host} abandoned after {time.monotonic() - started:.1f}s")
        if conn is not None:
            await conn.close()
        raise
    except Exception as exc:
        print(
            f"[DashProxy] proxy_dash error: {vid}:{idx} {media_type}/{qn}/{cid} {host} "
            f"after {time.monotonic() - started:.1f}s: {exc}"
        )
        if conn is not None:
            await conn.close()
        return Response("Upstream error", status=502)


