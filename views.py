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

import asyncio, requests
from flask import render_template, request
from bilibili_api import user, video, article, comment, search, homepage, video_zone

from shared import *
from extra import video_get_src_for_qn, video_get_dash_for_qn, bv2av, av2bv, article_to_html, article_to_any

@app.route('/licenses')
async def static_licenses_view():
    return await render_template_with_theme('licenses.html')

@app.route('/')
async def home_view():
    return await render_template_with_theme('home.html', i=await homepage.get_videos())

@app.route('/vv/<zid>')
@app.route('/vv/<zid>/')
async def zone_id_view(zid):
    pn = request.args.get('i') or 1
    info = await video_zone.get_zone_new_videos(zid, pn)
    return await render_template_with_theme('zone.html', info=info)

@app.route('/search')
async def search_view():
    q = request.args.get('q')
    i = request.args.get('i') or 1

    if not q:
        return await render_template_with_theme('error.html',
                                                status='无法搜索',
                                                desc='没有发送搜索关键字。',
                                                sg='请设置搜索关键字后重试。'), 400
    order_map = {
        'rank': search.OrderVideo.TOTALRANK,
        'click': search.OrderVideo.CLICK,
        'pubdate': search.OrderVideo.PUBDATE,
        'dm': search.OrderVideo.DM,
        'stow': search.OrderVideo.STOW,
        'scores': search.OrderVideo.SCORES,
        'attention': search.OrderArticle.ATTENTION,
        'fans': search.OrderUser.FANS,
        'level': search.OrderUser.LEVEL,
    }
    
    if request.args.get('t') == 'article':
        search_type = search.SearchObjectType.ARTICLE
        tmpl = 'search_article.html'
    elif request.args.get('t') == 'user':
        search_type = search.SearchObjectType.USER
        tmpl = 'search_user.html'
    else:
        search_type = search.SearchObjectType.VIDEO
        tmpl = 'search.html'

    sinfo = await search.search_by_type(q, page=i, search_type=search_type,
                                        order_type=order_map.get(request.args.get('sort')))
    return await render_template_with_theme(tmpl, q=q, sinfo=sinfo,
                                            rs=sinfo.get('result'),
                                            sort=request.args.get('sort'))

@app.route('/space/<mid>')
@app.route('/space/<mid>/')
async def space_view(mid):
    u = user.User(mid)
    uinfo, uvids = await asyncio.gather(u.get_user_info(),
                                        u.get_videos(pn=request.args.get('i') or 1, ps=28))
    return await render_template_with_theme('space.html', uinfo=uinfo, uvids=uvids)

@app.route('/author/<mid>')
@app.route('/author/<mid>/')
async def author_view(mid):
    u = user.User(mid)
    uinfo, uarticles = await asyncio.gather(u.get_user_info(),
                                        u.get_articles(pn=request.args.get('i') or 1, ps=28))
    return await render_template_with_theme('author.html', uinfo=uinfo, uarts=uarticles)

@app.route('/read/<cid>')
@app.route('/read/<cid>/')
@app.route('/read/mobile/<cid>')
@app.route('/read/mobile/<cid>/')
async def read_view(cid):
    req = requests.get(f'https://www.bilibili.com/read/{cid}',
                       headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
                                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0'
                                'Safari/537.36 Edg/111.0.1661.62'})

    if req.status_code != 200:
        st = '服务器错误'
        sg = None
        desc = '后端服务器发送了无效的回复'

        if req.status_code == 404:
            st = '没有找到文章'
            sg = '这很可能说明您访问的文章不存在，请检查您的请求。' \
                '如果您认为这是站点的问题，请联系网站管理员。'
        
        return await render_template_with_theme('error.html',
                                                status = st,
                                                desc = desc,
                                                suggest = sg), req.status_code

    if appconf['render']['use_pandoc'] and \
            request.args.get('format') in appconf['render']['article_allowed_formats']:
        return article_to_any(req.text, request.args.get('format'))
    else:
        ar = article.Article(cid[2:])
        try:
            return await render_template_with_theme('read.html', cid=cid, arinfo = (await ar.get_all())['readInfo'], article_content=article_to_html(req.text))
        except TypeError:
            return await render_template_with_theme('error.html',
                                                    status='没有找到文章',
                                                    desc='文章不存在',
                                                    sg = '这很可能说明您访问的文章不存在，请检查您的请求。'), 404

@app.route('/video_listen/<vid>')
@app.route('/video_listen/<vid>:<idx>')
@app.route('/video_listen/<vid>/')
@app.route('/video_listen/<vid>:<idx>/')
async def video_listen_view(vid, idx=0):
    ato = request.args.get('ato') == '1'
    
    # Convert ids to bvid for peace of mind.
    vid = av2bv(vid[2:]) if vid.startswith('av') else vid

    v = video.Video(bvid=vid, credential=appcred)
    
    vinfo, vtags, vrelated, vcomments, vset = \
        await asyncio.gather(v.get_info(), v.get_tags(idx), v.get_related(),
                             comment.get_comments(vid, comment.CommentResourceType.VIDEO, 1, comment.OrderType.LIKE), v.get_pages())

    # Store the download urls for proxies to use.
    if not appredis.exists(f'mikuinv_{vid}_{idx}_0'):
        for attempt in range(5):  # Retry up to 3 times
            vsrc = await video_get_dash_for_qn(v, idx)
            selected_audio_url = vsrc['dash']['audio'][0]['baseUrl']
        
            # Search for Akamai in the audio list
            for audio in vsrc['dash']['audio']:
                if 'akamai' in audio['baseUrl'] or 'akamaized.net' in audio['baseUrl']:
                    selected_audio_url = audio['baseUrl']
                    break

            # Check if we successfully found Akamai
            if 'akamai' in selected_audio_url or 'akamaized.net' in selected_audio_url:
                appredis.setex(f'mikuinv_{vid}_{idx}_0', 1800, selected_audio_url)
                break  # Success! Exit the retry loop
        
            # If not found and not on the last attempt, wait briefly before trying again
            if attempt < 2:
                await asyncio.sleep(2)
        

    return await render_template_with_theme('video_listen.html', vid=vid, vinfo=vinfo, vrelated=vrelated[:10], vcomments=vcomments,
                                            keywords = ','.join(map(lambda x: x['tag_name'], vtags)), ato=ato, idx=idx, vset=vset)

@app.route('/video/<vid>')
@app.route('/video/<vid>/')
@app.route('/video/<vid>:<idx>')
@app.route('/video/<vid>:<idx>/')
async def video_view(vid, idx=0):
    idx = int(idx)
    ato = request.args.get('ato') == '1'
    
    if request.args.get('listen') == '1':
        return await video_listen_view(vid, idx)

    # Convert ids to bvid for peace of mind.
    vid = av2bv(vid[2:]) if vid.startswith('av') else vid
    v = video.Video(bvid=vid, credential=appcred)

    # Fetch the download urls ahead of time to avoid blocking.
    v_supported_src, vinfo, vtags, vrelated, vcomments, vset = \
        await asyncio.gather(video_get_src_for_qn(v, idx), v.get_info(), v.get_tags(idx), v.get_related(),
                             comment.get_comments(vid, comment.CommentResourceType.VIDEO, 1, comment.OrderType.LIKE), v.get_pages())
    v_supported_src = v_supported_src['support_formats']
    
    # Store the download urls for proxies to use.
    if not appredis.exists(f'mikuinv_{vid}_{idx}_16'):
        for vsrc in await asyncio.gather(*[video_get_src_for_qn(v, idx, fmt['quality']) for fmt in v_supported_src]):
            qn = vsrc['quality']
            # Prioritize Akamai mirrors for better direct connection compatibility
            selected_url = vsrc['durl'][0]['url']
            for durl in vsrc['durl']:
                if 'akamai' in durl['url'] or 'akamaized.net' in durl['url']:
                    selected_url = durl['url']
                    break
            appredis.setex(f'mikuinv_{vid}_{idx}_{qn}', 1800, selected_url)
        for vsrc in v_supported_src:
            qn = vsrc['quality']
            if not appredis.exists(f'mikuinv_{vid}_{idx}_{qn}'):
                appredis.setex(f'mikuinv_{vid}_{idx}_{qn}', 1800, appredis.get(f'mikuinv_{vid}_{idx}_16'))

    return await render_template_with_theme('video.html', vid=vid, vinfo=vinfo, vcomments=vcomments, vrelated=vrelated[:15],
                                            keywords = ','.join(map(lambda x: x['tag_name'], vtags)),
                                            supported_src=v_supported_src, ato=ato, idx=idx, vset=vset)
