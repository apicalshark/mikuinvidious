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
import os
import re
import secrets
import sys
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import filters  # noqa: F401
import res  # noqa: F401
import views  # noqa: F401
from api import exceptions
from csrf import csrf_protect, inject_csrf_token
from proxy import proxy_bp
from quart import Response, g, jsonify, make_response, redirect, request, send_from_directory, url_for
from rate_limit import RATE_LIMITS, add_rate_limit_headers, rate_limit
from shared import (
    Network,
    app,
    appconf,
    close_global_client,
    render_template_with_theme,
)


async def monitor_fd():
    while True:
        try:
            # Count open file descriptors via /proc/self/fd (Linux specific)
            if os.path.exists("/proc/self/fd"):
                fd_count = len(os.listdir("/proc/self/fd"))
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                sys.stderr.write(f"[{timestamp}] Open FDs: {fd_count}\n")
                sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f"Error monitoring FDs: {e}\n")
            sys.stderr.flush()
        await asyncio.sleep(60)  # Check every 60 seconds


@app.before_serving
async def start_background_tasks():
    if appconf["server"]["monitor_fd"]:
        app.add_background_task(monitor_fd)


@app.after_serving
async def shutdown_cleanup():
    await close_global_client()


@app.before_request
async def setup_request():
    """Generate CSP nonce, resolve locale, and initialize history ID."""
    g.csp_nonce = secrets.token_urlsafe(16)

    from i18n import normalize_supported
    from shared import detect_locale

    g.locale = detect_locale()
    # Persist explicit ?lang= overrides so navigation keeps the choice. Only
    # offer locales this instance serves, never an arbitrary ?lang= value.
    query_lang = normalize_supported(request.args.get("lang"))
    g.set_lang_cookie = query_lang if query_lang and query_lang != request.cookies.get("lang") else None

    hist_id = request.cookies.get("hist_id")
    if not hist_id or not re.match(r"^[a-f0-9]{16}$", hist_id):
        g.hist_id = os.urandom(8).hex()
        g.set_hist_cookie = True
    else:
        g.hist_id = hist_id
        g.set_hist_cookie = False


@app.context_processor
def inject_csp_nonce():
    """Make CSP nonce and locale available to all templates."""
    from i18n import locale_choices

    return {
        "csp_nonce": getattr(g, "csp_nonce", ""),
        "locale": getattr(g, "locale", "en"),
        # Language menus render from the configured allowlist, not a hardcoded list.
        "locale_choices": locale_choices(),
    }


@app.after_request
async def set_hist_id(response):
    if getattr(g, "set_hist_cookie", False):
        is_secure = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
        response.set_cookie(
            "hist_id", g.hist_id, max_age=3600 * 24 * 30, httponly=True, samesite="Lax", secure=is_secure
        )
    lang = getattr(g, "set_lang_cookie", None)
    if lang:
        is_secure = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
        response.set_cookie("lang", lang, path="/", max_age=3600 * 24 * 30, samesite="Lax", secure=is_secure)
    return response


@app.after_request
async def add_security_headers(response):
    # Add rate limit headers
    response = await add_rate_limit_headers(response)
    # Add security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # L3: the app sits behind Caddy (plain HTTP app:8080), so
    # request.is_secure is always false in production. Honor the
    # X-Forwarded-Proto header like the hist_id cookie already does.
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    if request.is_secure or forwarded_proto == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

    # Skip CSP for AJAX/fragment requests (they have different nonces)
    # These are requests made via fetch/XHR that return HTML fragments
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.headers.get("Sec-Fetch-Mode") == "fetch"
        or request.headers.get("HX-Request") == "true"
        or request.path.startswith("/api/component/")
    )

    # Add CSP header with nonce (skip for AJAX fragment requests)
    if not is_ajax:
        csp_nonce = getattr(g, "csp_nonce", "")
        if csp_nonce:
            csp = (
                "default-src 'self'; "
                f"script-src 'self' 'nonce-{csp_nonce}'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "media-src 'self' blob:; "
                "font-src 'self' data:; "
                "connect-src 'self' wss: https:; "
                "worker-src 'self' blob:; "
                "base-uri 'self'; "
                "form-action 'self';"
            )
            response.headers["Content-Security-Policy"] = csp
    return response


app.register_blueprint(proxy_bp)

from dash_proxy import dash_proxy_bp

app.register_blueprint(dash_proxy_bp)

from views_bangumi import bangumi_bp

app.register_blueprint(bangumi_bp)

app.context_processor(inject_csrf_token)

##########################################
# APIs
##########################################


@app.route("/toggle_theme", methods=["POST"])
@csrf_protect()
@rate_limit(**RATE_LIMITS["normal"])
async def toggle_theme_api():
    old_val = request.cookies.get("dark-theme")
    if old_val == "1":
        new_val = "0"
    else:
        new_val = "1"

    print(f"[Theme] Toggling from {old_val} to {new_val}")
    resp = await make_response("OK")
    # Mirror the hist_id pattern: behind Caddy request.is_secure is always
    # false, so honor X-Forwarded-Proto for the Secure flag.
    forwarded = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    is_secure = request.is_secure or forwarded == "https"
    resp.set_cookie(
        "dark-theme", new_val, path="/", max_age=3600 * 24 * 30, httponly=True, samesite="Lax", secure=is_secure
    )
    return resp


@app.route("/set_lang", methods=["POST"])
@csrf_protect()
@rate_limit(**RATE_LIMITS["normal"])
async def set_lang_api():
    """Persist UI locale choice (mirrors /toggle_theme cookie pattern)."""
    from i18n import normalize_supported

    form = await request.form
    raw = form.get("lang") or request.args.get("lang") or ""
    lang = normalize_supported(raw)
    if lang is None:
        return Response("Unsupported language", status=400)
    print(f"[I18n] Setting language to {lang}")
    redirect_to = request.headers.get("Referer") or "/preferences"
    if redirect_to:
        parsed = urlparse(redirect_to)
        q = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "lang"]
        clean_query = urlencode(q)
        redirect_to = urlunparse(parsed._replace(query=clean_query))
    resp = redirect(redirect_to)
    forwarded = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    is_secure = request.is_secure or forwarded == "https"
    resp.set_cookie("lang", lang, path="/", max_age=3600 * 24 * 30, samesite="Lax", secure=is_secure)
    return resp


##########################################
# Additional features
##########################################


@app.route("/<b32tvid>")
@rate_limit(**RATE_LIMITS["normal"])
async def b32tv_redirect(b32tvid):
    # Validate b32tvid format (base32, typically 6-12 chars)
    import re

    if not re.match(r"^[A-Za-z0-9]{6,12}$", b32tvid):
        return Response("Invalid short link format", status=400)

    client = await Network.get_async_client()
    req = None
    try:
        _req = client.build_request("GET", f"https://b23.tv/{b32tvid}")
        req = await client.send(_req, follow_redirects=False)
        if req.status_code != 302:
            try:
                e = req.json()
                msg = e.get("message", "Unknown error")
                code = e.get("code", req.status_code)
            except Exception:
                msg = "Page not found" if req.status_code == 404 else "Unknown error"
                code = req.status_code

            return await render_template_with_theme(
                "error.html",
                status=msg,
                desc="The requested resource does not exist." if code == 404 else msg,
                suggest="Please check your request and try again.",
            ), abs(code)

        location = req.headers.get("Location")
        if not location:
            return await render_template_with_theme(
                "error.html",
                status="Parse error",
                desc="Failed to resolve the redirect target.",
                suggest="Please check the URL.",
            ), 500

        url = urlparse(location)
        if url.path.startswith("/read/mobile"):
            return redirect(url_for("read_view", cid=f"cv{url.path[13:]}"))
        elif url.path.startswith("/opus/"):
            return redirect(url_for("read_view", cid=f"opus{url.path[6:]}"))
        elif url.path.startswith("/video/"):
            return redirect(url_for("video_view", vid=location.split("/")[-1][:12]))
        elif "/audio/au" in url.path:
            return redirect(url_for("audio_view", auid="au" + url.path.split("/audio/au")[-1].split("?")[0]))
        elif "/audio/am" in url.path:
            return redirect(url_for("audio_list_view", amid="am" + url.path.split("/audio/am")[-1].split("?")[0]))
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"[Redirect] Error redirecting b23.tv/{b32tvid}: {e}")
        return await render_template_with_theme(
            "error.html",
            status="Network error",
            desc="Failed to resolve the short link. Please check your network connection or proxy settings.",
            suggest="Please check your network connection or proxy settings.",
        ), 500
    finally:
        if req:
            await req.aclose()


@app.route("/download", methods=["POST"])
@csrf_protect()
@rate_limit(**RATE_LIMITS["strict"])
async def dl_redirect():
    form = await request.form
    bvid = form.get("id")
    cvid = form.get("cvid")
    qual = form.get("qual")

    # Validate input to prevent open redirect
    import re

    if not bvid or not re.match(r"^(BV[a-zA-Z0-9]{10}|av\d+)$", bvid):
        return Response("Invalid video ID", status=400)
    if not cvid or not cvid.isdigit():
        return Response("Invalid page index", status=400)
    if not qual or not qual.isdigit():
        return Response("Invalid quality", status=400)

    # Muxed DASH download: resolves the best video (<=1080p anonymous cap) + audio
    # tracks and remuxes them into a single playable MP4 via ffmpeg.
    #
    # fetch/XHR callers (the floating download dialog) get a background job id
    # and poll /download/status/<job_id> instead of hanging on this response;
    # plain form posts keep the legacy 302 for no-JS clients.
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get(
        "Accept", ""
    ):
        from dash_proxy import DownloadCapacityError, create_download_job

        try:
            job_id = await create_download_job(bvid, int(cvid), int(qual))
        except DownloadCapacityError as exc:
            return jsonify({"error": str(exc)}), 429
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 403
        return jsonify({"job_id": job_id})
    return redirect(f"/proxy/download/{bvid}/{cvid}/{qual}", code=302)


##########################################
# Misc
##########################################


@app.route("/favicon.ico")
async def favicon():
    return await send_from_directory(app.static_folder, "favicon.ico")


@app.route("/site.webmanifest")
async def webmanifest():
    return await send_from_directory(
        app.static_folder, "site.webmanifest", mimetype="application/manifest+json", cache_timeout=86400
    )


@app.route("/preferences")
async def pref_view():
    return await render_template_with_theme("pref.html")


@app.route("/robots.txt")
async def robots_txt():
    policy = appconf["site"].get("robots_policy") or "strict"

    # Only allow 'strict' or 'relaxed' policies
    if policy not in ("strict", "relaxed"):
        policy = "strict"

    return await send_from_directory(os.path.join(app.root_path, "../static/rules"), f"robots_{policy}.txt")


##########################################
# Error handling
##########################################


@app.errorhandler(404)
async def not_found_error(e):
    return await render_template_with_theme(
        "error.html",
        status="Page not found (404)",
        desc="The requested page does not exist.",
        suggest="Please check the URL.",
    ), 404


@app.errorhandler(exceptions.ArgsException)
async def args_exception_view(e):
    # Sanitize argument error - don't expose internal details
    return await render_template_with_theme(
        "error.html", status="Bad request", desc="Invalid request parameters. Please check and try again."
    ), 400


@app.errorhandler(exceptions.ResponseCodeException)
async def resp_exception_view(e):
    suggest = None
    if e.code == -404:
        suggest = (
            "The video/article you requested most likely does not exist. "
            "Please check your request. If you believe this is a site issue, "
            "please contact the administrator."
        )

    # Sanitize error message - never expose raw backend response
    if appconf["site"]["site_show_unsafe_error_response"]:
        # Only show sanitized message even in debug mode
        desc = f"Backend error: {e.msg}"
    else:
        desc = "Backend server sent an invalid response."

    return await render_template_with_theme(
        "error.html",
        status=e.msg,
        desc=desc,
        suggest=suggest,
    ), -e.code


@app.errorhandler(Exception)
async def general_exception_view(e):
    # Log full error internally but show generic message to user
    import traceback

    error_msg = f"{type(e).__name__}: {e}"
    traceback.print_exc()
    print(f"[ERROR] {error_msg}")
    # Never expose internal error details to users
    return await render_template_with_theme(
        "error.html", status="Server error", desc="Internal server error. Please try again later or contact the administrator."
    ), 500
