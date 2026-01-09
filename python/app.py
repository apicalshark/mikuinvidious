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
from urllib.parse import urlparse

import filters  # noqa: F401
import res  # noqa: F401
import views  # noqa: F401
from bilibili_api import exceptions
from proxy import proxy_bp
from quart import make_response, redirect, request, send_from_directory, url_for
from shared import (
    Network,
    app,
    appconf,
    close_global_client,
    detect_theme,
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
        await asyncio.sleep(10)


@app.before_serving
async def start_background_tasks():
    app.add_background_task(monitor_fd)


@app.after_serving
async def shutdown_cleanup():
    await close_global_client()


@app.after_request
async def set_hist_id(response):
    if not request.cookies.get("hist_id"):
        hist_id = os.urandom(8).hex()
        response.set_cookie("hist_id", hist_id, max_age=3600 * 24 * 365, httponly=True, samesite="Lax")
    return response


app.register_blueprint(proxy_bp)

##########################################
# APIs
##########################################


@app.route("/toggle_theme")
async def toggle_theme_api():
    old_val = request.cookies.get("dark-theme")
    if old_val == "1":
        new_val = "0"
    else:
        new_val = "1"

    print(f"[Theme] Toggling from {old_val} to {new_val}")
    resp = await make_response("OK")
    resp.set_cookie("dark-theme", new_val, path="/", max_age=3600 * 24 * 365, httponly=True, samesite="Lax")
    return resp


##########################################
# Additional features
##########################################


@app.route("/<b32tvid>")
async def b32tv_redirect(b32tvid):
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
        elif url.path.startswith("/video/"):
            return redirect(url_for("video_view", vid=location.split("/")[-1][:12]))
        elif "/audio/au" in url.path:
            return redirect(url_for("audio_view", auid="au" + url.path.split("/audio/au")[-1].split("?")[0]))
        elif "/audio/am" in url.path:
            return redirect(url_for("audio_list_view", amid="am" + url.path.split("/audio/am")[-1].split("?")[0]))
    except Exception as e:
        print(f"[Redirect] Error redirecting b23.tv/{b32tvid}: {e}")
        return await render_template_with_theme(
            "error.html", status="网络错误", desc=str(e), suggest="请检查您的网络连接或代理设置。"
        ), 500
    finally:
        if req:
            await req.aclose()


@app.route("/download", methods=["POST"])
async def dl_redirect():
    form = await request.form
    bvid = form.get("id")
    cvid = form.get("cvid")
    qual = form.get("qual")
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


@app.route("/test")
async def test_view():
    theme = detect_theme()
    resp = await make_response(theme)
    resp.set_cookie("theme", "default", httponly=True, samesite="Lax")
    return resp


@app.route("/robots.txt")
async def robots_txt():
    policy = appconf["site"].get("robots_policy") or "strict"

    if policy == "PLEASE_INDEX_EVERYTHING":
        return "", 404

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
    return await render_template_with_theme("error.html", status="请求错误", desc=e), 400


@app.errorhandler(exceptions.ResponseCodeException)
async def resp_exception_view(e):
    suggest = None
    if e.code == -404:
        suggest = "这很可能说明您访问的视频/文章不存在，请检查您的请求。如果您认为这是站点的问题，请联系网站管理员。"

    return await render_template_with_theme(
        "error.html",
        status=e.msg,
        desc=(e.raw if appconf["site"]["site_show_unsafe_error_response"] else "后端服务器发送了无效的回复。"),
        suggest=suggest,
    ), -e.code


@app.errorhandler(Exception)
async def general_exception_view(e):
    error_msg = f"{type(e).__name__}: {e}"
    print(error_msg)
    return await render_template_with_theme("error.html", status="服务器错误", desc=error_msg), 500
