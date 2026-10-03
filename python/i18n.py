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
"""Locale detection + JSON catalog helpers.

Source of truth is PO files under ``locales/<lang>/LC_MESSAGES/messages.po``.
At build time ``tools/po2json.py`` compiles them to:
- ``messages.json`` (lightweight catalog used for server-side templates and client JS)

Locale resolution order: ``?lang=`` > ``lang`` cookie > ``Accept-Language`` >
``[display] default_locale``. Proxied upstream content (video titles,
comments, danmaku, Bilibili filter vocab, Nyaa titles) is never translated;
only UI chrome goes through ``_()``.
"""

from __future__ import annotations

import json
import os

LOCALES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "locales")

# Endonyms shown in the language menus (a locale is named in its own language,
# so these are display labels, not translatable strings).
LOCALE_LABELS = {
    "en": "English",
    "zh-CN": "简体中文",
    "zh-TW": "繁體中文",
    "ja": "日本語",
}
# Fallback spec when no allowlist is configured.
DEFAULT_SUPPORTED_LOCALES = "en,zh-CN,zh-TW,ja"
# Filesystem directory names used by locales (BCP-47 with underscore).
_LOCALE_TO_DIR = {
    "en": "en",
    "zh-cn": "zh_CN",
    "zh-tw": "zh_TW",
    "ja": "ja",
    "zh": "zh_CN",
}

_JSON_CACHE: dict[str, dict[str, str]] = {}

# Parsed + normalized allowlist of selectable locales, in configured order.
# Installed once by set_supported_locales() (env at import, then the merged
# config.toml value from shared) and read through supported_locales() so every
# consumer — resolution, /set_lang validation, template menus — shares one list.
_SUPPORTED: tuple[str, ...] = ()


def parse_supported_locales(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated locale spec into canonical tags (deduped, ordered)."""
    if not raw:
        return ()
    out: list[str] = []
    for part in raw.split(","):
        canonical = normalize_locale(part)
        if canonical is not None and canonical not in out:
            out.append(canonical)
    return tuple(out)


def set_supported_locales(raw: str | None) -> tuple[str, ...]:
    """Install the allowlist from a raw spec; unparsable specs fall back to "en"."""
    global _SUPPORTED
    _SUPPORTED = parse_supported_locales(raw) or ("en",)
    return _SUPPORTED


def supported_locales() -> tuple[str, ...]:
    """Return the canonical locales that may be selected."""
    return _SUPPORTED


def locale_choices() -> list[tuple[str, str]]:
    """Return ``(tag, label)`` pairs for the language menus, in allowlist order."""
    return [(locale, LOCALE_LABELS.get(locale, locale)) for locale in _SUPPORTED]


def normalize_locale(value: str | None) -> str | None:
    """Normalize a raw locale string to a canonical supported tag."""
    if not value:
        return None
    v = value.strip().replace("_", "-").lower()
    # Strip charset suffixes like "zh-CN.UTF-8".
    v = v.split(".")[0]
    # Exact match first (covers zh-cn, zh-tw).
    if v in _LOCALE_TO_DIR:
        canonical = {"en": "en", "zh-cn": "zh-CN", "zh-tw": "zh-TW", "ja": "ja", "zh": "zh-CN"}[v]
        return canonical
    # Bare language fallback: "en-*" -> "en", "ja-*" -> "ja".
    base = v.split("-")[0]
    if base == "en":
        return "en"
    if base == "ja":
        return "ja"
    if base == "zh":
        # Traditional variants default to zh-TW, everything else to zh-CN.
        if any(t in v for t in ("hant", "hk", "tw", "mo")):
            return "zh-TW"
        return "zh-CN"
    return None


def normalize_supported(value: str | None) -> str | None:
    """Normalize a raw locale string and enforce the allowlist.

    Returns None when the input normalizes to nothing or to a locale the
    instance does not offer, so callers never fall through to an unmanaged tag.
    """
    canonical = normalize_locale(value)
    if canonical is not None and canonical in _SUPPORTED:
        return canonical
    return None


def parse_accept_language(header: str | None) -> str | None:
    """Pick the best supported locale from an Accept-Language header."""
    if not header:
        return None
    candidates: list[tuple[float, str]] = []
    for part in header.split(","):
        part = part.strip()
        if not part:
            continue
        lang = part.split(";")[0].strip()
        q = 1.0
        if ";q=" in part or "; q=" in part:
            try:
                q = float(part.split("q=")[-1].split(";")[0].strip())
            except ValueError:
                q = 0.0
        norm = normalize_supported(lang)
        if norm is not None:
            candidates.append((q, norm))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def detect_locale(query_lang: str | None, cookie_lang: str | None, accept_header: str | None, default: str) -> str:
    """Resolve locale with priority: ?lang= > cookie > Accept-Language > default.

    Every candidate must clear the allowlist; a locale the instance does not
    offer is ignored rather than honoured (it falls through to the next source).
    """
    for candidate in (
        normalize_supported(query_lang) if query_lang else None,
        normalize_supported(cookie_lang) if cookie_lang else None,
    ):
        if candidate is not None:
            return candidate
    parsed = parse_accept_language(accept_header)
    if parsed is not None:
        return parsed
    # The configured default may be outside the allowlist; fall back to the
    # first offered locale so a render never lands on an unmanaged tag.
    return normalize_supported(default) or (_SUPPORTED[0] if _SUPPORTED else "en")


def _locale_dir(locale: str) -> str:
    return _LOCALE_TO_DIR.get(locale.lower(), "en")


def gettext_msg(locale: str, msgid: str) -> str:
    """Translate a msgid for the given locale (falls back to msgid)."""
    if not msgid:
        return ""
    catalog = get_json_catalog(locale)
    return catalog.get(msgid) or msgid


def ngettext_msg(locale: str, singular: str, plural: str, n: int) -> str:
    """Plural-aware translation (falls back to English singular/plural).

    English picks by n, CJK locales use the single translated form (or plural lookup) for every n.
    """
    canonical = normalize_supported(locale) or "en"
    if canonical == "en":
        return singular if n == 1 else plural
    catalog = get_json_catalog(canonical)
    single = catalog.get(singular)
    if single and single != singular:
        return single
    multi = catalog.get(plural)
    if multi and multi != plural:
        return multi
    return singular if n == 1 else plural


def get_json_catalog(locale: str) -> dict[str, str]:
    """Return the lightweight JSON catalog for JS (cached, English fallback)."""
    canonical = normalize_supported(locale) or "en"
    if canonical in _JSON_CACHE:
        return _JSON_CACHE[canonical]
    path = os.path.join(LOCALES_DIR, _locale_dir(canonical), "LC_MESSAGES", "messages.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            catalog = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        catalog = {}
    _JSON_CACHE[canonical] = catalog
    return catalog


# Seed the allowlist from the environment so this module is usable standalone;
# shared re-installs it from the merged appconf (config.toml wins) once the
# config has been loaded.
set_supported_locales(os.environ.get("SUPPORTED_LOCALES") or DEFAULT_SUPPORTED_LOCALES)
