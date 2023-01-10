# /* app.py
#  * Copyright (C) 2023 MikuInvidious Team
#  *
#  * This software is free software; you can redistribute it and/or
#  * modify it under the terms of the GNU General Public License as
#  * published by the Free Software Foundation; either version 3 of
#  * the License, or (at your option) any later version.
#  *
#  * This software is distributed in the hope that it will be useful,
#  * but WITHOUT ANY WARRANTY; without even the implied warranty of
#  * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  * General Public License for more details.
#  *
#  * You should have received a copy of the GNU General Public License
#  * along with this library. If not, see <http://www.gnu.org/licenses/>.
#  */

from vidconv import av2bv
from bbapi import *

from datetime import datetime
from urllib.parse import urlparse
import requests
import flask
import json
import os
import math

from conf import *

os.environ['TZ'] = 'Asia/Shanghai'
app = flask.Flask(__name__)

# Bultin Flask Cache-Control
@app.after_request
def add_cache_control(resp):
	try:
		resp.cache_control.max_age = cache_time
	except:
		resp.cache_control.max_age = 0
	return resp

# Bultiin Bilibili Video Source Proxy
@app.route('/proxy/vid/<path:url>')
def proxy(url):
	netloc = urlparse(url).netloc
	if not (netloc.endswith('.bilivideo.com') or netloc.endswith('-mirrorakam.akamaized.net')):
		return 'bad', 404

	resp_headers = { 'Accept-Ranges': 'bytes', }
	req_headers = safe_headers

	if 'Range' in flask.request.headers:
		req_headers['Range'] = flask.request.headers['Range']
		resp_headers['Range'] = flask.request.headers['Range']

	req = requests.get(url, stream = True, headers = safe_headers,
	                   params = flask.request.args, cookies=cookies)

	resp_headers['Content-Length'] = req.headers['Content-Length']

	if 'Range' in flask.request.headers:
		resp_headers['Content-Range'] = req.headers['Content-Range']
	return flask.Response(flask.stream_with_context(req.iter_content(8192)),
	                      content_type = req.headers['Content-Type'],
	                      status = req.status_code,
	                      headers = resp_headers)

@app.route('/error/<code>')
@app.route('/error/<code>/<id>')
def error(id = '', code = ''):
	return 'oops'

@app.route('/')
def front_page():
	req = requests.get('https://s.search.bilibili.com/main/hotword')
	result_json = req.json()
	result = []

	if result_json['code'] == 0:
		result = result_json['list']

	return flask.render_template('index.html', hotwards = result)

@app.route('/<b32tvid>')
def b32tv_redirect(b32tvid):
	req = requests.get(f'https://b23.tv/{b32tvid}', allow_redirects=False)
	if req.status_code != 302:
		return 'oops', req.status_code
	return flask.redirect(flask.url_for('video_page', vid = req.headers['Location'].split('/')[-1][:12]))

@app.route('/vvinfo/<vid>:<cid>:<qn>')
def video_view_info(vid, cid, qn = 16):
	# Convert AVid to BVid to simplify handling.
	if vid.lower().startswith('av'):
		vid = av2bv(int(vid[2:]))

	srcinfo = bbapi_src_from_bvid(cid, vid, qn)

	if srcinfo['code'] != 0:
		return ''
	if not (srcinfo['data']['format'] == 'mp4' or srcinfo['data']['format'] == 'mp4720'):
		return ''

	return f'window.srcinfo[{qn}] = ' + json.dumps(srcinfo['data']) + ';'

@app.route('/search')
@app.route('/search/<kwards>')
def search_page(kwards = None):
	pn = 1
	if 'pn' in flask.request.args:
		pn = flask.request.args['pn']

	if 'kwards' in flask.request.args:
		vids = bbapi_vids_from_kward(flask.request.args['kwards'], pn)
	elif kwards:
		vids = bbapi_vids_from_kward(kwards, pn)
	else:
		return 'oops', 404

	return flask.render_template('search.html', result=vids['data'])

@app.route('/space/<mid>')
@app.route('/space/<mid>:<page_num>')
def member_page(mid, page_num = 1):
	page_num = int(page_num)

	if 'p' in flask.request.args:
		page_num = flask.request.args['p']

	vids = bbapi_vids_from_mid_paging(mid, page_num)
	info = bbapi_uinfo_from_mid(mid)
	card = bbapi_ucard_from_mid(mid)

	return flask.render_template('space.html', vids = vids['data'], page = vids['data']['page'],
	                             info = info['data'], card = card['data'])

@app.route('/video/<vid>')
@app.route('/video/<vid>/')
def video_page(vid):
	# Convert AVid to BVid to simplify handling.
	if vid.lower().startswith('av'):
		vid = av2bv(int(vid[2:]))

	vidinfo = bbapi_info_from_bvid(vid)
	if vidinfo['code'] != 0:
		return flask.redirect(flask.url_for('error', code = vidinfo['code'], id = vid))	

	relatedvids = bbapi_related_from_bvid(vid)
	if relatedvids['code'] != 0:
		relatedvids['data'] = []
	else:
		relatedvids['data'] = relatedvids['data'][:12]

	cums = bbapi_cum_from_bvid(vid)
	if cums['code'] != 0:
		cums['data'] = {}
		cums['data']['replies'] = []
	elif not cums['data']['replies']:
		cums['data']['replies'] = []

	# Convert AVid to BVid to simplify handling.
	if vid.lower().startswith('av'):
		vid = av2bv(int(vid[2:]))

	if 'qn' in flask.request.args:
		qn = flask.request.args['qn']
	else:
		qn = 16 # 320P

	srcinfo = bbapi_src_from_bvid(vidinfo['data']['cid'], vid, qn)
	if srcinfo['code'] != 0:
		return flask.redirect(flask.url_for('error', code = srcinfo['code'], id = vid))	 

	return flask.render_template('video.html', vidinfo = vidinfo['data'], srcinfo = srcinfo['data'],
	                             relatedvids = relatedvids['data'], comments = cums['data'])

# Template filiters
@app.template_filter('date')
def _jinja2_filter_datetime(ts, fmt='%Y年%m月%d日 %H点%m分'):
	return datetime.fromtimestamp(1577835803).strftime(fmt)

@app.template_filter('proxy')
def _jinja2_filter_proxy(url):
	return flask.url_for('proxy', url = url)

@app.template_filter('hexcolor')
def _jinja2_filter_hexcolor(num):
	return str(hex(int(num)))[2:].upper()

@app.template_filter('ceil')
def _jinja2_filter_ceil(num):
	return int(math.ceil(float(num)))

if __name__ == '__main__':
	app.debug = True
	app.run()
