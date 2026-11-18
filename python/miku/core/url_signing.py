# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stateless HMAC-SHA256 URL signing and verification for derived resource routes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html as html_lib
import re
from urllib.parse import quote

_SIGNING_CONTEXT = b"miku-url-signing-v1"
_SIG_BYTES = 16
_SIG_LEN = 22
_SIG_RE = re.compile(rf"^[A-Za-z0-9_-]{{{_SIG_LEN}}}$")
_SCHEME_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_PIC_SIGNED_PREFIX = "/proxy/pic/s/"
_PIC_RAW_PREFIX = "/proxy/pic/"
_SAFE_PATH_CHARS = "/%:@!$&'()*+,;=-._~"
_SAFE_QUERY_CHARS = "/?:@!$&()*+,;=-._~%"

_HTML_PIC_RE = re.compile(r'(["\'])/proxy/pic/(?:s/[A-Za-z0-9_-]{22}/)?([^"\'\s<>]+)\1')

_cached_secret: str | bytes | None = None
_cached_subkey: bytes | None = None


def _get_signing_key() -> bytes:
    """Derive and cache the domain-separated HMAC subkey from ``app.secret_key``."""
    global _cached_secret, _cached_subkey
    from miku.state import app

    raw_secret = app.secret_key or ""
    if raw_secret == _cached_secret and _cached_subkey is not None:
        return _cached_subkey

    secret_bytes = raw_secret.encode("utf-8") if isinstance(raw_secret, str) else bytes(raw_secret)
    subkey = hmac.new(secret_bytes, _SIGNING_CONTEXT, hashlib.sha256).digest()
    _cached_secret = raw_secret
    _cached_subkey = subkey
    return subkey


def _compute_sig(namespace: str, payload: str) -> str:
    msg = f"{namespace}:{payload}".encode()
    digest = hmac.new(_get_signing_key(), msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:_SIG_BYTES]).rstrip(b"=").decode("ascii")


def _verify_sig(sig: str | None, namespace: str, payload: str) -> bool:
    if not isinstance(sig, str) or not _SIG_RE.fullmatch(sig):
        return False
    return hmac.compare_digest(sig, _compute_sig(namespace, payload))


def _canonicalize_pic_target(
    target: str,
    query_string: str | None = None,
    *,
    encode_query: bool = False,
) -> tuple[str, str, str]:
    if query_string is None and "?" in target:
        path_part, qs = target.split("?", 1)
    else:
        path_part, qs = target, (query_string or "")

    encoded_path = quote(path_part, safe=_SAFE_PATH_CHARS)
    final_qs = quote(qs, safe=_SAFE_QUERY_CHARS) if (encode_query and qs) else qs
    hmac_input = f"{encoded_path}\x00{final_qs}"
    return encoded_path, final_qs, hmac_input


def sign_pic_target(target: str) -> str:
    """Sign a scheme-less ``<host><path>[?query]`` target as ``/proxy/pic/s/<sig>/<target>``."""
    cleaned = target.lstrip("/")
    if not cleaned:
        return ""
    encoded_path, qs, hmac_input = _canonicalize_pic_target(cleaned, encode_query=True)
    signed = f"{_PIC_SIGNED_PREFIX}{_compute_sig('pic', hmac_input)}/{encoded_path}"
    return f"{signed}?{qs}" if qs else signed


def build_signed_pic_url(raw_url: str) -> str:
    """Normalize an upstream image URL (or host/path) and return a signed ``/proxy/pic/s/...`` URL."""
    if not raw_url:
        return ""
    cleaned = raw_url.split("#", 1)[0]
    scheme_match = _SCHEME_PREFIX_RE.match(cleaned)
    if scheme_match:
        target = cleaned[scheme_match.end() :]
    elif cleaned.startswith("//"):
        target = cleaned[2:]
    else:
        target = cleaned
    if target.endswith("?"):
        target = target[:-1]
    return sign_pic_target(target)


def verify_signed_pic_subpath(subpath: str, query_string: str = "") -> str | None:
    """Verify ``s/<sig>/<target>`` (plus optional query_string) and return canonical ``<target>`` if valid."""
    if not subpath.startswith("s/"):
        return None
    rest = subpath[2:]
    slash_idx = rest.find("/")
    if slash_idx != _SIG_LEN:
        return None
    sig = rest[:slash_idx]
    target = rest[slash_idx + 1 :]
    if not target:
        return None

    encoded_path, _qs, hmac_input = _canonicalize_pic_target(target, query_string=query_string)
    if not _verify_sig(sig, "pic", hmac_input):
        return None
    return encoded_path


def resign_pic_url(pic_url: str) -> str:
    """Re-sign a single ``/proxy/pic/...`` URL string if it matches the proxy prefix."""
    if not pic_url or not pic_url.startswith(_PIC_RAW_PREFIX):
        return pic_url
    rest = pic_url[len(_PIC_RAW_PREFIX) :]
    if rest.startswith("s/"):
        slash = rest.find("/", 2)
        if slash == 2 + _SIG_LEN:
            rest = rest[slash + 1 :]
    return sign_pic_target(rest)


def resign_pic_urls_in_html(html_text: str) -> str:
    """Re-sign any ``/proxy/pic/...`` attribute URLs inside cached article HTML."""
    if not html_text or _PIC_RAW_PREFIX not in html_text:
        return html_text

    def _replace(match: re.Match[str]) -> str:
        quote_char = match.group(1)
        raw_target = html_lib.unescape(match.group(2))
        signed = sign_pic_target(raw_target)
        return f"{quote_char}{html_lib.escape(signed, quote=True)}{quote_char}"

    return _HTML_PIC_RE.sub(_replace, html_text)


def sign_danmaku_url(vid: str, idx: int = 0) -> str:
    """Sign ``/res/danmaku/s/<sig>/<vid>:<idx>``."""
    sig = _compute_sig("danmaku", f"{vid}:{int(idx)}")
    return f"/res/danmaku/s/{sig}/{vid}:{int(idx)}"


def verify_danmaku_sig(sig: str | None, vid: str, idx: int = 0) -> bool:
    """Verify a danmaku resource signature."""
    try:
        idx_int = int(idx)
    except TypeError, ValueError:
        return False
    return _verify_sig(sig, "danmaku", f"{vid}:{idx_int}")


def sign_subtitle_list_url(vid: str, idx: int = 0) -> str:
    """Sign ``/res/subtitle/s/<sig>/<vid>:<idx>``."""
    sig = _compute_sig("sublist", f"{vid}:{int(idx)}")
    return f"/res/subtitle/s/{sig}/{vid}:{int(idx)}"


def verify_subtitle_list_sig(sig: str | None, vid: str, idx: int = 0) -> bool:
    """Verify a subtitle-list resource signature."""
    try:
        idx_int = int(idx)
    except TypeError, ValueError:
        return False
    return _verify_sig(sig, "sublist", f"{vid}:{idx_int}")


def sign_subtitle_vtt_url(vid: str, idx: int, lan: str) -> str:
    """Sign ``/res/subtitle/s/<sig>/<vid>:<idx>:<lan>``."""
    sig = _compute_sig("subvtt", f"{vid}:{int(idx)}:{lan}")
    return f"/res/subtitle/s/{sig}/{vid}:{int(idx)}:{lan}"


def verify_subtitle_vtt_sig(sig: str | None, vid: str, idx: int, lan: str) -> bool:
    """Verify a subtitle-VTT resource signature."""
    try:
        idx_int = int(idx)
    except TypeError, ValueError:
        return False
    return _verify_sig(sig, "subvtt", f"{vid}:{idx_int}:{lan}")


def sign_dash_manifest_url(vid: str, idx: int) -> str:
    """Sign ``/video/dash/s/<sig>/<vid>/<idx>/manifest.mpd``."""
    sig = _compute_sig("mpd", f"{vid}:{int(idx)}")
    return f"/video/dash/s/{sig}/{vid}/{int(idx)}/manifest.mpd"


def verify_dash_manifest_sig(sig: str | None, vid: str, idx: int) -> bool:
    """Verify a DASH manifest signature."""
    try:
        idx_int = int(idx)
    except TypeError, ValueError:
        return False
    return _verify_sig(sig, "mpd", f"{vid}:{idx_int}")


def sign_dash_track_url(vid: str, idx: int, media_type: str, qn: int, cid: int) -> str:
    """Sign ``/proxy/dash/s/<sig>/<vid>/<idx>/<media_type>/<qn>/<cid>``."""
    payload = f"{vid}:{int(idx)}:{media_type}:{int(qn)}:{int(cid)}"
    sig = _compute_sig("dash", payload)
    return f"/proxy/dash/s/{sig}/{vid}/{int(idx)}/{media_type}/{int(qn)}/{int(cid)}"


def verify_dash_track_sig(sig: str | None, vid: str, idx: int, media_type: str, qn: int, cid: int) -> bool:
    """Verify a DASH track proxy signature."""
    try:
        payload = f"{vid}:{int(idx)}:{media_type}:{int(qn)}:{int(cid)}"
    except TypeError, ValueError:
        return False
    return _verify_sig(sig, "dash", payload)
