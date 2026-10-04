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

Source of truth is PO files under ``locales/<lang>/LC_MESSAGES/messages.po``
(``<lang>`` is BCP-47 with underscores, e.g. ``zh_CN`` for ``zh-CN``).
At build time ``tools/po2json.py`` compiles them to:
- ``messages.json`` (lightweight catalog used for server-side templates and client JS)

Adding a locale is just: create
``locales/<lang>/LC_MESSAGES/messages.po`` (copy ``locales/messages.pot``),
translate it, run ``npm run build:i18n``. No code changes needed — supported
locales are auto-discovered from that directory, and menu labels fall back to
Babel's endonym when no explicit override exists below.

Locale resolution order: ``?lang=`` > ``lang`` cookie > ``Accept-Language`` >
``[display] default_locale``. Proxied upstream content (video titles,
comments, danmaku, Bilibili filter vocab, Nyaa titles) is never translated;
only UI chrome goes through ``_()``.
"""

from __future__ import annotations

import json
import os

LOCALES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "locales")

# Explicit endonym overrides shown in the language menus (a locale is named in
# its own language, so these are display labels, not translatable strings).
# Locales without an entry here fall back to Babel's endonym
# (``Locale.get_display_name``), then to the raw tag.
LOCALE_LABELS = {
    "en": "English",
    "zh-CN": "简体中文",
    "zh-TW": "繁體中文",
    "ja": "日本語",
}
# Legacy fallback spec; kept for backwards compatibility. When no allowlist is
# configured (env/config unset), supported locales are auto-discovered from
# ``locales/*/LC_MESSAGES/messages.po`` instead.
DEFAULT_SUPPORTED_LOCALES = "en,zh-CN,zh-TW,ja"
# Legacy directory overrides for locale dirs that do not follow the default
# ``tag.replace("-", "_")`` mapping. New locales never need an entry here.
_LOCALE_TO_DIR = {
    "en": "en",
    "zh-cn": "zh_CN",
    "zh-tw": "zh_TW",
    "ja": "ja",
    "zh": "zh_CN",
}

_JSON_CACHE: dict[str, dict] = {}
# Locales without plural forms: the single translated form is used for every n.
_CJK_BASES = frozenset({"zh", "ja", "ko"})

# Parsed + normalized allowlist of selectable locales, in configured order.
# Installed once by set_supported_locales() (env at import, then the merged
# config.toml value from shared) and read through supported_locales() so every
# consumer — resolution, /set_lang validation, template menus — shares one list.
_SUPPORTED: tuple[str, ...] = ()


def _canonicalize_tag(value: str) -> str | None:
    """Canonicalize a raw locale string to BCP-47 form (no allowlist check).

    ``fr_fr.UTF-8`` -> ``fr-FR``, ``ZH-cn`` -> ``zh-CN``, ``en`` -> ``en``.
    Returns None for empty input.
    """
    v = value.strip().replace("_", "-")
    # Strip charset suffixes like "zh-CN.UTF-8".
    v = v.split(".")[0].strip()
    if not v:
        return None
    parts = [p for p in v.split("-") if p]
    if not parts:
        return None
    out = [parts[0].lower()]
    for p in parts[1:]:
        if len(p) == 2:
            out.append(p.upper())
        elif len(p) == 4:
            out.append(p.title())
        else:
            out.append(p.lower())
    return "-".join(out)


def _dir_to_tag(dirname: str) -> str | None:
    """Convert a ``locales/`` directory name to its canonical tag."""
    return _canonicalize_tag(dirname.replace("_", "-"))


def discover_available_locales() -> tuple[str, ...]:
    """List canonical locale tags found under ``locales/*/LC_MESSAGES/``.

    A locale counts when its directory holds a ``messages.po`` or a compiled
    ``messages.json``. Sorted for deterministic menus; the configured
    allowlist (when set) controls the final order instead.
    """
    found: list[str] = []
    try:
        entries = sorted(os.listdir(LOCALES_DIR))
    except OSError:
        return ()
    for name in entries:
        if name.startswith((".", "_")):
            continue
        lc_dir = os.path.join(LOCALES_DIR, name, "LC_MESSAGES")
        try:
            has_po = os.path.isfile(os.path.join(lc_dir, "messages.po"))
            has_json = os.path.isfile(os.path.join(lc_dir, "messages.json"))
        except OSError:
            continue
        if not (has_po or has_json):
            continue
        tag = _dir_to_tag(name)
        if tag is not None and tag not in found:
            found.append(tag)
    return tuple(found)


def _display_label(locale: str) -> str:
    """Endonym for a locale: explicit override, else Babel, else raw tag."""
    if locale in LOCALE_LABELS:
        return LOCALE_LABELS[locale]
    try:
        from babel import Locale

        return Locale.parse(locale.replace("-", "_")).get_display_name(locale)
    except Exception:
        return LOCALE_LABELS.get(locale.lower(), locale)


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
    """Install the allowlist.

    An explicit spec (env/config) wins; an empty/unset spec auto-discovers
    from ``locales/``; with neither, fall back to ``("en",)``.
    """
    global _SUPPORTED
    _SUPPORTED = parse_supported_locales(raw) or discover_available_locales() or ("en",)
    return _SUPPORTED


def supported_locales() -> tuple[str, ...]:
    """Return the canonical locales that may be selected."""
    return _SUPPORTED


def locale_choices() -> list[tuple[str, str]]:
    """Return ``(tag, label)`` pairs for the language menus, in allowlist order."""
    return [(locale, _display_label(locale)) for locale in _SUPPORTED]


def normalize_locale(value: str | None) -> str | None:
    """Normalize a raw locale string to canonical BCP-47 (no allowlist check)."""
    if not value:
        return None
    canonical = _canonicalize_tag(value)
    if canonical is None:
        return None
    # Bare "zh" defaults to Simplified, mirroring historical behaviour.
    if canonical.lower() == "zh":
        return "zh-CN"
    return canonical


def _base_candidates(base: str) -> list[str]:
    """Supported locales sharing a base language subtag, in allowlist order."""
    base = base.lower()
    return [loc for loc in _SUPPORTED if loc.split("-")[0].lower() == base]


def normalize_supported(value: str | None) -> str | None:
    """Normalize a raw locale string and enforce the allowlist.

    Exact match first; then base-language fallback (``fr-FR`` -> ``fr``,
    ``en-US`` -> ``en``); Chinese Traditional/Simplified heuristics pick
    ``zh-TW`` vs ``zh-CN`` when the exact variant is not offered. Returns None
    when nothing matches, so callers fall through to the next locale source.
    """
    canonical = normalize_locale(value)
    if canonical is None:
        return None
    if canonical in _SUPPORTED:
        return canonical
    base = canonical.split("-")[0]
    candidates = _base_candidates(base)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if base.lower() == "zh":
        # Prefer Traditional for Hant/HK/TW/MO requests, Simplified otherwise.
        want_trad = any(t in canonical.lower() for t in ("hant", "hk", "tw", "mo"))
        prefer = "zh-TW" if want_trad else "zh-CN"
        if prefer in candidates:
            return prefer
    return candidates[0]


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
    key = locale.strip().replace("_", "-").lower()
    if key in _LOCALE_TO_DIR:
        return _LOCALE_TO_DIR[key]
    canonical = normalize_locale(locale) or "en"
    return canonical.replace("-", "_")


def _plural_tag(locale: str, n: int) -> str:
    """CLDR plural category for n in a locale (Babel; safe fallback)."""
    try:
        from babel import Locale

        return Locale.parse(locale.replace("-", "_")).plural_form(n)
    except Exception:
        return "one" if n == 1 else "other"


def gettext_msg(locale: str, msgid: str) -> str:
    """Translate a msgid for the given locale (falls back to msgid)."""
    if not msgid:
        return ""
    catalog = get_json_catalog(locale)
    val = catalog.get(msgid)
    if isinstance(val, list):
        return val[0] or msgid
    if isinstance(val, str):
        return val or msgid
    return msgid


def ngettext_msg(locale: str, singular: str, plural: str, n: int) -> str:
    """Plural-aware translation (falls back to English singular/plural).

    Plural entries in the catalog carry every ``msgstr[]`` form; the right one
    is picked via the catalog's CLDR category order (built by
    ``tools/po2json.py``), so three-form locales (uk, ru, pl, ...) resolve
    exactly. Locales without plural data keep the legacy behaviour (CJK-style
    locales use the single translated form for every n, others pick by n).
    """
    canonical = normalize_supported(locale) or "en"
    catalog = get_json_catalog(canonical)
    val = catalog.get(singular)
    if isinstance(val, list) and val:
        order = catalog.get("__plural", {}).get("order", ["one", "other"])
        tag = _plural_tag(canonical, n)
        idx = order.index(tag) if tag in order else (0 if n == 1 else len(val) - 1)
        idx = max(0, min(idx, len(val) - 1))
        return val[idx] or val[0] or (singular if n == 1 else plural)
    if canonical.split("-")[0].lower() in _CJK_BASES:
        single = catalog.get(singular)
        if isinstance(single, str) and single and single != singular:
            return single
        multi = catalog.get(plural)
        if isinstance(multi, str) and multi and multi != plural:
            return multi
        return singular if n == 1 else plural
    if n == 1:
        single = catalog.get(singular)
        return single if isinstance(single, str) and single else singular
    multi = catalog.get(plural)
    return multi if isinstance(multi, str) and multi else plural


def get_json_catalog(locale: str) -> dict:
    """Return the lightweight JSON catalog for JS (cached, English fallback).

    Values are usually strings; plural entries are form arrays under the
    singular msgid, plus a ``__plural`` metadata entry (see tools/po2json.py).
    """
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
# config has been loaded. Unset/empty means auto-discover from locales/.
set_supported_locales(os.environ.get("SUPPORTED_LOCALES") or None)
