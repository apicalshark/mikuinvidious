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
import sys
from datetime import datetime
from urllib.parse import urlparse

import filters  # noqa: F401
import res  # noqa: F401
import views  # noqa: F401
from bilibili_api import exceptions
from proxy import proxy_bp
import secrets
from quart import make_response, redirect, request, send_from_directory, url_for, abort, g
from shared import (
    Network,
    app,
    appconf,
    close_global_client,
    detect_theme,
    render_template_with_theme,
)
from csrf import csrf_protect, inject_csrf_token, get_csrf_token
from rate_limit import rate_limit, add_rate_limit_headers, RATE_LIMITS


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
    app.add_background_task(monitor_fd)


@app.after_serving
async def shutdown_cleanup():
    await close_global_client()


@app.before_request
async def generate_csp_nonce():
    """Generate CSP nonce for inline scripts."""
    g.csp_nonce = secrets.token_urlsafe(16)


@app.context_processor
def inject_csp_nonce():
    """Make CSP nonce available to all templates."""
    return {"csp_nonce": getattr(g, 'csp_nonce', '')}


@app.after_request
async def set_hist_id(response):
    if not request.cookies.get("hist_id"):
        hist_id = os.urandom(8).hex()
        # 30 days max-age, rotated on each response if older than 15 days
        response.set_cookie("hist_id", hist_id, max_age=3600 * 24 * 30, httponly=True, samesite="Lax", secure=request.is_secure)
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
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    
    # Skip CSP for AJAX/fragment requests (they have different nonces)
    # These are requests made via fetch/XHR that return HTML fragments
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or \
              request.headers.get("Sec-Fetch-Mode") == "fetch" or \
              request.headers.get("HX-Request") == "true" or \
              request.path.startswith("/api/component/")
    
    # Add CSP header with nonce (skip for AJAX fragment requests)
    if not is_ajax:
        csp_nonce = getattr(g, 'csp_nonce', '')
        if csp_nonce:
            csp = (
                "default-src 'self'; "
                "script-src 'self' 'nonce-{}'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "media-src 'self' blob:; "
                "font-src 'self' data:; "
                "connect-src 'self' wss: https:; "
                "worker-src 'self' blob:; "
                "base-uri 'self'; "
                "form-action 'self';"
            ).format(csp_nonce)
            response.headers["Content-Security-Policy"] = csp
    return response


app.register_blueprint(proxy_bp)

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
    resp.set_cookie("dark-theme", new_val, path="/", max_age=3600 * 24 * 30, httponly=True, samesite="Lax", secure=request.is_secure)
    return resp


##########################################
# Additional features
##########################################


@app.route("/<b32tvid>")
@rate_limit(**RATE_LIMITS["normal"])
async def b32tv_redirect(b32tvid):
    # Validate b32tvid format (base32, typically 6-12 chars)
    import re
    if not re.match(r'^[A-Za-z0-9]{6,12}$', b32tvid):
        abort(400, description="Invalid short link format")
    
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
                msg = "未找到页面" if req.status_code == 404 else "未知错误"
                code = req.status_code

            return await render_template_with_theme(
                "error.html",
                status=msg,
                desc="您请求的资源不存在。" if code == 404 else msg,
                suggest="请检查您的请求并重试。",
            ), abs(code)

        location = req.headers.get("Location")
        if not location:
            return await render_template_with_theme(
                "error.html", status="解析错误", desc="无法获取重定向地址。", suggest="请检查网址是否正确。"
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
            "error.html", status="网络错误", desc="无法解析短链接，请检查网络连接或代理设置。", suggest="请检查您的网络连接或代理设置。"
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
    if not bvid or not re.match(r'^(BV[a-zA-Z0-9]{10}|av\d+)$', bvid):
        abort(400, description="Invalid video ID")
    if not cvid or not cvid.isdigit():
        abort(400, description="Invalid page index")
    if not qual or not qual.isdigit():
        abort(400, description="Invalid quality")
    
    return redirect(f"/proxy/video/{bvid}_{cvid}_{qual}?dl=1", code=302)


##########################################
# Misc
##########################################


@app.route("/favicon.ico")
async def favicon():
    return "", 204


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
        "error.html", status="未找到页面 (404)", desc="您请求的页面不存在。", suggest="请检查 URL 是否正确。"
    ), 404


@app.errorhandler(exceptions.ArgsException)
async def args_exception_view(e):
    # Sanitize argument error - don't expose internal details
    return await render_template_with_theme("error.html", status="请求错误", desc="请求参数无效，请检查后重试。"), 400


@app.errorhandler(exceptions.ResponseCodeException)
async def resp_exception_view(e):
    suggest = None
    if e.code == -404:
        suggest = "这很可能说明您访问的视频/文章不存在，请检查您的请求。如果您认为这是站点的问题，请联系网站管理员。"

    # Sanitize error message - never expose raw backend response
    if appconf["site"]["site_show_unsafe_error_response"]:
        # Only show sanitized message even in debug mode
        desc = f"后端错误: {e.msg}"
    else:
        desc = "后端服务器发送了无效的回复。"

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
        "error.html", 
        status="服务器错误", 
        desc="服务器内部错误，请稍后重试或联系管理员。"
    ), 500
