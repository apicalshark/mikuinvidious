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

from urllib.parse import urlparse

import asyncio
from flask import request, make_response, redirect, url_for, send_from_directory, session

import subprocess
from bs4 import BeautifulSoup

from shared import *
from views import *
from res import *
from filters import *
from extra import video_get_src_for_qn, video_get_dash_for_qn, bv2av, av2bv
from proxy import proxy_bp

import traceback
from bilibili_api import exceptions

app.secret_key = appconf['admin']['secret_key']

@app.route('/login', methods=['GET', 'POST'])
async def login_view():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if (appconf['admin']['username'] and 
            username == appconf['admin']['username'] and 
            password == appconf['admin']['password']):
            session['is_admin'] = True
            return redirect('/')
        return await render_template_with_theme('login.html', error='Invalid credentials'), 401
    return await render_template_with_theme('login.html')

@app.route('/logout')
def logout_view():
    session.pop('is_admin', None)
    return redirect('/')

app.register_blueprint(proxy_bp)

##########################################
# APIs
##########################################

@app.route('/toggle_theme')
def toggle_theme_api():
    resp = make_response()

    if dark_theme := request.cookies.get('dark-theme'):
        dark_theme = not int(dark_theme)
    else:
        dark_theme = False

    resp.set_cookie('dark-theme', '1' if dark_theme else '0')
    return resp

@app.route('/toggle_opencc')
def toggle_opencc_api():
    resp = make_response()

    if opencc := request.cookies.get('opencc'):
        opencc = not int(opencc)
    else:
        opencc = True

    resp.set_cookie('opencc', '1' if opencc else '0', path='/')
    return resp

##########################################
# Additional features
##########################################

@app.route('/<b32tvid>')
async def b32tv_redirect(b32tvid):
    client = get_global_httpx_client()
    try:
        req = await client.get(f'https://b23.tv/{b32tvid}', follow_redirects=False)
    except Exception as e:
        return await render_template_with_theme('error.html',
                                          status = '网络错误',
                                          desc = str(e),
                                          suggest='請檢查您的網絡連接或代理設置。'), 500
    
    if req.status_code != 302:
        try:
            e = req.json()
            msg = e.get('message', 'Unknown error')
            code = e.get('code', 500)
        except:
            msg = 'Unknown error'
            code = 500
        return await render_template_with_theme('error.html',
                                          status = msg,
                                          desc = msg,
                                          suggest='请检查您的请求并重试。'),  abs(code)

    url = urlparse(req.headers['Location'])
    if url.path.startswith('/read/mobile'):
        return redirect(url_for('read_view', cid = f'cv{url.path[13:]}'))
    elif url.path.startswith('/video/'):
        return redirect(url_for('video_view', vid = req.headers['Location'].split('/')[-1][:12]))
    elif '/audio/au' in url.path:
        return redirect(url_for('audio_view', auid = 'au' + url.path.split('/audio/au')[-1].split('?')[0]))
    elif '/audio/am' in url.path:
        return redirect(url_for('audio_list_view', amid = 'am' + url.path.split('/audio/am')[-1].split('?')[0]))


@app.route('/download', methods=['POST'])
def dl_redirect():
    bvid = request.form.get('id')
    cvid = request.form.get('cvid')
    qual = request.form.get('qual')
    return redirect(f'/proxy/video/{bvid}_{cvid}_{qual}', code=302)
    
##########################################
# Misc
##########################################

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/preferences')
async def pref_view():
    return await render_template_with_theme('pref.html')

@app.route('/test')
async def test_view():
    theme = detect_theme()
    resp = make_response(theme)
    resp.set_cookie('theme', 'default')
    return resp

@app.route('/robots.txt')
def robots_txt():
    policy = appconf['site'].get('robots_policy') or 'strict'
    
    if policy == 'PLEASE_INDEX_EVERYTHING':
        return '', 404
    
    return send_from_directory(f'static/rules', f'robots_{policy}.txt')

##########################################
# Error handling
##########################################

@app.errorhandler(exceptions.ArgsException)
async def args_exception_view(e):
    return await render_template_with_theme('error.html',
                                            status = '请求错误',
                                            desc = e), 400

@app.errorhandler(exceptions.ResponseCodeException)
async def resp_exception_view(e):
    suggest = None
    if e.code == -404:
        suggest = '这很可能说明您访问的视频/文章不存在，请检查您的请求。' \
            '如果您认为这是站点的问题，请联系网站管理员。'
    
    return await render_template_with_theme('error.html',
                                            status = e.msg,
                                            desc = (e.raw if appconf['site']['site_show_unsafe_error_response'] else f'后端服务器发送了无效的回复。'), suggest=suggest),  -e.code

@app.errorhandler(Exception)
async def general_exception_view(e):
    error_msg = f'{type(e).__name__}: {e}'
    print(error_msg)
    return await render_template_with_theme('error.html', status='服务器错误', desc=error_msg), 500
