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

import asyncio, os
from flask import render_template, request
from bilibili_api import user, video, article, comment, search, homepage, video_zone, audio, opus

from shared import *
from extra import video_get_src_for_qn, video_get_dash_for_qn, bv2av, av2bv, article_to_html, article_to_any, get_article_info

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
@app.route('/opus/<cid>')
@app.route('/opus/<cid>/')
async def read_view(cid):
    is_opus = 'opus' in request.path or not cid.startswith('cv')
    
    if is_opus:
        url = f'https://www.bilibili.com/opus/{cid.replace("opus", "")}'
    else:
        url = f'https://www.bilibili.com/read/{cid}'

    client = get_global_httpx_client()
    try:
        req = await client.get(url,
                       headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
                                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0'
                                'Safari/537.36 Edg/111.0.1661.62'},
                       follow_redirects=True)
    except Exception as e:
         return await render_template_with_theme('error.html',
                                            status = '网络错误',
                                            desc = str(e),
                                            suggest = '請檢查您的網絡連接或代理設置。'), 500

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
        cvid = cid.replace('cv', '').replace('opus', '')
        try:
            # Use INITIAL_STATE for most info as bilibili_api's get_all is broken
            arinfo = get_article_info(req.text, cid)
            
            # Supplement with get_info() for stats if possible
            try:
                if is_opus:
                    o = opus.Opus(int(cvid))
                    api_info = await o.get_info()
                    # opus.Opus info structure is different, stats are in modules
                    for module in api_info.get('item', {}).get('modules', []):
                        if module.get('module_stat'):
                            stat = module['module_stat']
                            arinfo['stats']['like'] = stat.get('like', {}).get('count', arinfo['stats']['like'])
                            arinfo['stats']['coin'] = stat.get('coin', {}).get('count', arinfo['stats']['coin'])
                            arinfo['stats']['favorite'] = stat.get('favorite', {}).get('count', arinfo['stats']['favorite'])
                            arinfo['stats']['share'] = stat.get('forward', {}).get('count', arinfo['stats']['share'])
                else:
                    ar = article.Article(int(cvid))
                    api_info = await ar.get_info()
                    if api_info.get('stats'):
                        arinfo['stats'].update(api_info['stats'])
                    if api_info.get('title') and not arinfo['title']:
                        arinfo['title'] = api_info['title']
            except:
                pass

            return await render_template_with_theme('read.html', cid=cid, arinfo = arinfo, article_content=article_to_html(req.text), is_opus=is_opus)
        except Exception as e:
            print(f"Error rendering article: {e}")
            return await render_template_with_theme('error.html',
                                                    status='没有找到文章',
                                                    desc='文章不存在或解析错误',
                                                    sg = '這很可能說明您訪問的文章不存在，請檢查您的請求。'), 404

@app.route('/video_listen/<vid>')
@app.route('/video_listen/<vid>:<idx>')
@app.route('/video_listen/<vid>/')
@app.route('/video_listen/<vid>:<idx>/')
async def video_listen_view(vid, idx=0):
    ato = request.args.get('ato') == '1'
    idx = int(idx)
    vid = av2bv(vid[2:]) if vid.startswith('av') else vid
    v = video.Video(bvid=vid, credential=appcred)
    
    async def get_audio_url():
        if not appredis.exists(f'mikuinv_{vid}_{idx}_0'):
            try:
                vsrc = await video_get_dash_for_qn(v, idx)
                if 'dash' in vsrc and 'audio' in vsrc['dash'] and vsrc['dash']['audio']:
                    appredis.setex(f'mikuinv_{vid}_{idx}_0', 1800, vsrc['dash']['audio'][0]['baseUrl'])
                    return
            except: pass
            try:
                vsrc_fallback = await video_get_src_for_qn(v, idx, 16)
                if 'durl' in vsrc_fallback and vsrc_fallback['durl']:
                    appredis.setex(f'mikuinv_{vid}_{idx}_0', 1800, vsrc_fallback['durl'][0]['url'])
            except: pass

    results = await asyncio.gather(
        v.get_info(), v.get_tags(idx), v.get_related(),
        comment.get_comments(vid, comment.CommentResourceType.VIDEO, 1, comment.OrderType.LIKE), 
        v.get_pages(), get_audio_url(),
        return_exceptions=True
    )
    
    vinfo = results[0] if not isinstance(results[0], Exception) else {'title': vid, 'stat': {'view':0,'like':0,'coin':0,'favorite':0,'share':0}, 'owner':{'name':'Unknown','mid':0,'face':''}, 'desc':'', 'pubdate':0, 'tid':0, 'tname':''}
    vtags = results[1] if not isinstance(results[1], Exception) else []
    vrelated = results[2] if not isinstance(results[2], Exception) else []
    vcomments = results[3] if not isinstance(results[3], Exception) else {'page':{'count':0}, 'replies':[]}
    vset = results[4] if not isinstance(results[4], Exception) else [{'page':1, 'part':vid}]

    return await render_template_with_theme('video_listen.html', vid=vid, vinfo=vinfo, vrelated=vrelated[:10], vcomments=vcomments,
                                            keywords = ','.join(map(lambda x: x.get('tag_name', ''), vtags)), ato=ato, idx=idx, vset=vset)

@app.route('/video/<vid>')
@app.route('/video/<vid>/')
@app.route('/video/<vid>:<idx>')
@app.route('/video/<vid>:<idx>/')
async def video_view(vid, idx=0):
    idx = int(idx)
    ato = request.args.get('ato') == '1'
    if request.args.get('listen') == '1': return await video_listen_view(vid, idx)

    vid = av2bv(vid[2:]) if vid.startswith('av') else vid
    v = video.Video(bvid=vid, credential=appcred)

    try:
        v_supported_src_res = await video_get_src_for_qn(v, idx)
        v_supported_src = v_supported_src_res.get('support_formats', [])
    except: v_supported_src = []

    async def cache_video_urls():
        if v_supported_src and not appredis.exists(f'mikuinv_{vid}_{idx}_16'):
            q_results = await asyncio.gather(*[video_get_src_for_qn(v, idx, fmt['quality']) for fmt in v_supported_src], return_exceptions=True)
            for vsrc in q_results:
                if not isinstance(vsrc, Exception) and 'durl' in vsrc and vsrc['durl']:
                    appredis.setex(f'mikuinv_{vid}_{idx}_{vsrc["quality"]}', 1800, vsrc['durl'][0]['url'])

    results = await asyncio.gather(
        v.get_info(), v.get_tags(idx), v.get_related(),
        comment.get_comments(vid, comment.CommentResourceType.VIDEO, 1, comment.OrderType.LIKE), 
        v.get_pages(), cache_video_urls(),
        return_exceptions=True
    )

    vinfo = results[0] if not isinstance(results[0], Exception) else {'title': vid, 'bvid':vid, 'stat': {'view':0,'like':0,'coin':0,'favorite':0,'share':0}, 'owner':{'name':'Unknown','mid':0,'face':''}, 'desc':'', 'pubdate':0, 'tid':0, 'tname':''}
    vtags = results[1] if not isinstance(results[1], Exception) else []
    vrelated = results[2] if not isinstance(results[2], Exception) else []
    vcomments = results[3] if not isinstance(results[3], Exception) else {'page':{'count':0}, 'replies':[]}
    vset = results[4] if not isinstance(results[4], Exception) else [{'page':1, 'part':vid}]

    return await render_template_with_theme('video.html', vid=vid, vinfo=vinfo, vcomments=vcomments, vrelated=vrelated[:15],
                                            keywords = ','.join(map(lambda x: x.get('tag_name', ''), vtags)),
                                            supported_src=v_supported_src, ato=ato, idx=idx, vset=vset)

@app.route('/audio/<auid>')
async def audio_view(auid):
    ato = request.args.get('ato') == '1'
    auid_int = int(auid[2:]) if auid.startswith('au') else int(auid)
    a = audio.Audio(auid_int, credential=appcred)
    
    async def get_audio_url():
        if not appredis.exists(f'mikuinv_{auid}_{0}_0'):
            try:
                asrc = await a.get_download_url()
                if 'cdns' in asrc and asrc['cdns']:
                    appredis.setex(f'mikuinv_{auid}_{0}_0', 1800, asrc['cdns'][0])
                elif 'url' in asrc:
                    appredis.setex(f'mikuinv_{auid}_{0}_0', 1800, asrc['url'])
            except: pass

    results = await asyncio.gather(
        a.get_info(), 
        comment.get_comments(auid_int, comment.CommentResourceType.AUDIO, 1, comment.OrderType.LIKE),
        get_audio_url(),
        return_exceptions=True
    )
    
    ainfo = results[0] if not isinstance(results[0], Exception) else {}
    # Map ainfo to vinfo format for template compatibility
    vinfo = {
        'title': ainfo.get('title', auid),
        'pic': ainfo.get('cover', ''),
        'desc': ainfo.get('intro', ''),
        'owner': {'name': ainfo.get('author', 'Unknown'), 'mid': ainfo.get('mid', 0), 'face': ''},
        'stat': {
            'view': ainfo.get('statistic', {}).get('play', 0),
            'like': ainfo.get('statistic', {}).get('collect', 0), # No direct like?
            'coin': ainfo.get('statistic', {}).get('coin', 0),
            'favorite': ainfo.get('statistic', {}).get('collect', 0),
            'share': ainfo.get('statistic', {}).get('share', 0)
        },
        'pubdate': ainfo.get('passtime', 0),
        'bvid': auid,
        'tid': 0, 'tname': 'Audio'
    }
    acomments = results[1] if not isinstance(results[1], Exception) else {'page':{'count':0}, 'replies':[]}
    
    return await render_template_with_theme('video_listen.html', vid=auid, vinfo=vinfo, vrelated=[], vcomments=acomments,
                                            keywords = '', ato=ato, idx=0, vset=[{'page':1, 'part':auid}])

@app.route('/audio_list/<amid>')
@app.route('/audio_list/<amid>:<idx>')
async def audio_list_view(amid, idx=0):
    idx = int(idx)
    ato = request.args.get('ato') == '1'
    amid_int = int(amid[2:]) if amid.startswith('am') else int(amid)
    al = audio.AudioList(amid_int, credential=appcred)
    
    songs_res = await al.get_song_list()
    songs = songs_res.get('data', [])
    if not songs or idx >= len(songs):
        return await render_template_with_theme('error.html', status='歌单为空', desc='没有找到歌曲'), 404
    
    current_song = songs[idx]
    auid = f"au{current_song['id']}"
    
    # Reuse audio_view logic for the current song
    # but we want to show the playlist (vset)
    
    a = audio.Audio(current_song['id'], credential=appcred)
    
    async def get_audio_url():
        if not appredis.exists(f'mikuinv_{amid}_{idx}_0'):
            try:
                asrc = await a.get_download_url()
                if 'cdns' in asrc and asrc['cdns']:
                    appredis.setex(f'mikuinv_{amid}_{idx}_0', 1800, asrc['cdns'][0])
                elif 'url' in asrc:
                    appredis.setex(f'mikuinv_{amid}_{idx}_0', 1800, asrc['url'])
            except: pass

    results = await asyncio.gather(
        a.get_info(),
        al.get_info(),
        comment.get_comments(current_song['id'], comment.CommentResourceType.AUDIO, 1, comment.OrderType.LIKE),
        get_audio_url(),
        return_exceptions=True
    )
    
    ainfo = results[0] if not isinstance(results[0], Exception) else {}
    list_info = results[1] if not isinstance(results[1], Exception) else {}
    
    vinfo = {
        'title': ainfo.get('title', auid),
        'pic': ainfo.get('cover', ''),
        'desc': ainfo.get('intro', ''),
        'owner': {'name': ainfo.get('author', 'Unknown'), 'mid': ainfo.get('mid', 0), 'face': ''},
        'stat': {
            'view': ainfo.get('statistic', {}).get('play', 0),
            'like': ainfo.get('statistic', {}).get('collect', 0),
            'coin': ainfo.get('statistic', {}).get('coin', 0),
            'favorite': ainfo.get('statistic', {}).get('collect', 0),
            'share': ainfo.get('statistic', {}).get('share', 0)
        },
        'pubdate': ainfo.get('passtime', 0),
        'bvid': auid,
        'tid': 0, 'tname': list_info.get('title', 'Audio List')
    }
    acomments = results[2] if not isinstance(results[2], Exception) else {'page':{'count':0}, 'replies':[]}
    
    vset = []
    for i, s in enumerate(songs):
        vset.append({
            'page': i + 1,
            'part': s.get('title', f"Song {i+1}"),
            'duration': s.get('duration', 0),
            'first_frame': s.get('cover', '')
        })
        
    return await render_template_with_theme('video_listen.html', vid=amid, vinfo=vinfo, vrelated=[], vcomments=acomments,
                                            keywords = '', ato=ato, idx=idx, vset=vset)


