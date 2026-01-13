from quart import Blueprint, request, jsonify
import shared
from bilibili_api import bangumi
from nyaa import search_nyaa
import asyncio
import re
import json
import os
from datetime import datetime
from extra import av2bv

bangumi_bp = Blueprint('bangumi', __name__, url_prefix='/bangumi')

# 加載篩選器配置
params_path = os.path.join(os.path.dirname(bangumi.__file__), 'data', 'bangumi_index_params.json')
try:
    with open(params_path, 'r', encoding='utf-8') as f:
        INDEX_PARAMS = json.load(f)
except:
    INDEX_PARAMS = {}

@bangumi_bp.route('/')
async def bangumi_home():
    stype = request.args.get('type', 'anime')
    if stype not in INDEX_PARAMS:
        stype = 'anime'
    
    current_params = INDEX_PARAMS[stype]
    filters = {}
    
    for f_item in current_params.get('filters', []):
        val = request.args.get(f_item['key'], '-1')
        if val.lstrip('-').isdigit():
            filters[f_item['key']] = int(val)
        else:
            filters[f_item['key']] = val

    order = request.args.get('order', '3')
    pn = int(request.args.get('page', 1))

    from bilibili_api.utils.network import Api
    api_info = bangumi.API["info"]["index"]
    
    ss_type = int(current_params['ssType'])
    
    query_params = {
        "type": ss_type,
        "season_type": ss_type,
        "order": int(order) if order.isdigit() else 3,
        "sort": 0,
        "page": pn,
        "pagesize": 20
    }
    query_params.update(filters)

    try:
        res = await Api(**api_info, credential=shared.appcred).update_params(**query_params).result
    except Exception as e:
        print(f"[Bangumi] Index API Error: {e}")
        res = {'list': [], 'has_next': 0}

    return await shared.render_template_with_theme(
        "bangumi_index.html",
        bangumi_list=res.get('list', []),
        has_next=res.get('has_next', 0),
        filter_meta=current_params.get('filters', []),
        order_meta=current_params.get('orders', []),
        current_filters={k: str(v) for k, v in filters.items()},
        current_order=order,
        current_type=stype,
        page=pn
    )

@bangumi_bp.route('/view/<int:ssid>')
async def bangumi_view(ssid):
    b = bangumi.Bangumi(ssid=ssid, credential=shared.appcred)
    try:
        # 直接獲取元數據與劇集列表
        meta_data = await asyncio.wait_for(b.get_meta(), timeout=10.0)
        if not meta_data:
            raise Exception("B站未返回有效的番劇元數據")
            
        meta = meta_data.get('media', {})
        raw_eps = []
        
        try:
            eps_data = await b.get_episode_list()
            if eps_data:
                # 遍歷所有可能的劇集存放位置 (標準、合集、專欄)
                raw_eps = eps_data.get('main_section', {}).get('episodes', [])
                if not raw_eps:
                    for section in eps_data.get('section', []):
                        raw_eps.extend(section.get('episodes', []))
        except Exception as e_eps:
            print(f"[Bangumi] Warning: Could not fetch episodes for ssid {ssid}: {e_eps}")
            
        if not raw_eps and meta:
            raw_eps = meta.get('episodes', [])
            
        if not raw_eps and not meta:
            raise Exception("B站返回了空的番劇數據，可能該內容已失效或受到地區限制。")

        episodes = []
        for ep_item in raw_eps:
            episodes.append({
                'title': ep_item.get('title'),
                'long_title': ep_item.get('long_title'),
                'bvid': ep_item.get('bvid') or (av2bv(ep_item.get('aid')) if ep_item.get('aid') else None),
                'aid': ep_item.get('aid'),
                'ep_id': ep_item.get('id')
            })
    except Exception as e:
        print(f"[Bangumi] Error fetching ssid {ssid}: {e}")
        return await shared.render_template_with_theme(
            "error.html", 
            status="番剧加载失败", 
            desc="后端服务器发送了无效的回复",
            suggest=f"錯誤訊息：{str(e)}。這通常是因為該內容在您所在的地區不可用，或已被 B 站下架。"
        )
    
    # 返回基礎頁面，Nyaa 搜尋移至前端 API
    return await shared.render_template_with_theme(
        "bangumi_view.html",
        meta=meta,
        episodes=episodes,
        ssid=ssid,
        nyaa_enabled=shared.appconf["site"]["nyaa_bangumi"]
    )

@bangumi_bp.route('/play/ep<int:ep_id>')
async def bangumi_play(ep_id):
    from bilibili_api.utils.network import Api
    
    # 1. Fetch Season Info using ep_id
    cred = shared.appcred
    has_sess = cred and cred.sessdata
    
    api = Api(
        "https://api.bilibili.com/pgc/view/web/season",
        "GET",
        verify=(not not has_sess),
        credential=cred
    )
    api.params = {"ep_id": ep_id}
    
    try:
        data = await api.request()
        # Fix: API might return unwrapped result
        res = data.get('result', data)
        
        # 2. Extract Metadata
        # Find the specific episode
        eps = res.get('episodes', [])
        current_ep = next((e for e in eps if e['id'] == ep_id), None)
        
        if not current_ep:
             for section in res.get('section', []):
                 for ep in section.get('episodes', []):
                     if ep['id'] == ep_id:
                         current_ep = ep
                         break
                 if current_ep: break
        
        if not current_ep:
            raise Exception("Episode not found in season data")

        bvid = current_ep.get('bvid')
        cid = current_ep.get('cid')
        ssid = res.get('season_id')
        
        # Parse pubdate to timestamp (int)
        pub_time_str = res.get('publish', {}).get('pub_time', '')
        pub_ts = 0
        if pub_time_str:
            try:
                dt = datetime.strptime(pub_time_str, "%Y-%m-%d %H:%M:%S")
                pub_ts = int(dt.timestamp())
            except Exception:
                pub_ts = 0

        def safe_int(v, default=0):
            try:
                if v == '--' or v is None: return default
                return int(v)
            except:
                return default

        # Construct vinfo for video.html
        vinfo = {
            "title": f"{res.get('title', '')} - {current_ep.get('title', '')}",
            "desc": res.get('evaluate', 'No description'),
            "pic": current_ep.get('cover') or res.get('cover'),
            "owner": {"name": "Bangumi (Official)", "mid": 0, "face": ""}, 
            "stat": {
                "view": safe_int(res.get('stat', {}).get('views')), 
                "like": safe_int(res.get('stat', {}).get('likes')),
                "coin": safe_int(res.get('stat', {}).get('coins')),
                "favorite": safe_int(res.get('stat', {}).get('favorites')),
                "share": safe_int(res.get('stat', {}).get('share'))
            },
            "pubdate": pub_ts,
            "bvid": bvid,
            "cid": cid, 
            "duration": safe_int(current_ep.get('duration'))
        }
        
        # Construct vset (episode list)
        vset = [{"page": 1, "part": current_ep.get('long_title', current_ep.get('title'))}]

        # Related Videos (Other episodes from all sections)
        all_eps = res.get('episodes', []).copy()
        for section in res.get('section', []):
            all_eps.extend(section.get('episodes', []))

        vrelated = []
        for ep in all_eps:
            if ep['id'] == ep_id: continue
            vrelated.append({
                "pic": ep.get('cover'),
                "title": f"{ep.get('title')} - {ep.get('long_title')}",
                "owner": {"name": "Bangumi"},
                "stat": {"view": 0, "danmaku": 0},
                "duration": ep.get('duration', 0),
                "bvid": f"ep{ep['id']}"
            })

    except Exception as e:
        print(f"[Bangumi] Play Error: {e}")
        return await shared.render_template_with_theme(
            "error.html", 
            status="番剧加载失败", 
            desc=str(e),
            suggest="请检查网络或稍后重试。"
        )

    return await shared.render_template_with_theme(
        "video.html",
        vid=bvid,
        vinfo=vinfo,
        vcomments={"page": {"count": 0}, "replies": []},
        vrelated=vrelated[:20],
        keywords="",
        supported_src=[], 
        ato=False,
        idx=0,
        vset=vset,
        is_live=False,
        ep_id=ep_id,
        ssid=ssid
    )

@bangumi_bp.route('/api/nyaa/<int:ssid>')
async def bangumi_nyaa_api(ssid):
    if not shared.appconf["site"]["nyaa_bangumi"]:
        return jsonify({'sidebar_html': '', 'ep_torrents': {}})

    # 1. 獲取標題
    b = bangumi.Bangumi(ssid=ssid, credential=shared.appcred)
    meta_data = await asyncio.wait_for(b.get_meta(), timeout=5.0)
    meta = meta_data.get('media', {}) if meta_data else {}
    raw_title = meta.get('title', '')
    
    if not raw_title:
        return ""

    # 2. 清理搜尋詞
    raw_title = meta.get('title', '')
    # 移除括號內容 (如：僅限港澳台、第二季)
    search_query = re.sub(r'[\(（].*?[\)）]', ' ', raw_title)
    # 移除特殊標點，保留空格
    search_query = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9\s]', ' ', search_query)
    # 壓縮空格
    search_query = re.sub(r'\s+', ' ', search_query).strip()
    
    # 如果標題太長 (超過 10 個字)，嘗試只取前 10 個字作為關鍵詞以增加匹配率
    if len(search_query) > 10:
        simplified_query = search_query[:10].strip()
    else: simplified_query = search_query

    # 構造帶有中文標籤的搜尋詞 (Nyaa 支援括號聯集搜尋)
    # 加入常用的中文標籤和知名中文小組，確保回傳結果包含中文資源
    cn_tags = "(CHT|CHS|繁|简|BIG5|喵萌|VCB|LoliHouse|极影|动漫国|幻樱|BeanSub|Lilith|JasinChen)"
    final_query = f"{simplified_query} {cn_tags}"

    # 3. 執行搜尋
    # 第一次嘗試：簡化標題 + 中文標籤 + 信任資源
    torrents = await search_nyaa(final_query, trusted_only=True, max_pages=3)
    is_fallback = False
    
    if not torrents:
        # 第二次嘗試：放寬到非信任資源 (很多字幕組不是 Trusted)
        torrents = await search_nyaa(final_query, trusted_only=False, max_pages=3)
        is_fallback = True
        
    if not torrents and simplified_query != search_query:
        # 第三次嘗試：完整標題 (不加標籤，最後的保底)
        torrents = await search_nyaa(search_query, trusted_only=False, max_pages=3)

    # 4. 分類與過濾邏輯
    def is_chinese_resource(title):
        # 1. 如果包含中文字符，極大機率是中文資源
        if re.search(r'[\u4e00-\u9fa5]', title):
            return True
        # 2. 檢查常見的中文標籤
        if re.search(r'CHT|CHS|繁|简|BIG5|GB|CHT&CHS|CHS&CHT', title, re.I):
            return True
        # 3. 知名中文小組或關鍵字
        chinese_keywords = ['喵萌', 'VCB', 'LoliHouse', '抽風', '千夏', '極影', '動漫國', '漫遊', '幻櫻', '悠哈', '豌豆', '風之聖殿', 'BeanSub', 'Lilith']
        for kw in chinese_keywords:
            if kw.lower() in title.lower():
                return True
        # 4. 排除明確標註了其他語言但沒寫中文的資源 (如 Erai-raws 的多國語言包)
        # 如果標題包含 [POR-BR], [SPA-LA], [RUS] 等但沒通過上述檢查，則視為非中文
        if re.search(r'\[POR-BR\]|\[SPA-LA\]|\[RUS\]|\[FRA\]|\[GER\]', title, re.I):
            return False
            
        return False

    def extract_episode(title):
        clean_title = re.sub(
            r'10-?bit|Hi10[Pp]?|1080[Pp]|720[Pp]|4[Kk]|2[Kk]|[Hh][. ]?26[45]|[Xx]26[45]|[Vv][Cc][Bb]-?[Ss]tudio|'
            r'[Bb][Dd][Rr]ip|[Ww][Ee][Bb]-?[Dd][Ll]|[Bb]lu-?ray|[Rr]eseed|[Mm]ulti-?[Aa]udio|'
            r'\d{4}年|\d{1,2}月(?:新番|番)|[Hh][Cc]|'
            r'\[\d{4}[.\-/]\d{2}[.\-/]\d{2}\]', ' ', title, flags=re.I
        )
        if re.search(r'\[Fin\]|Complete|全[集部]|合集|剧[场場]版|Movie|OVA|SP|\d{1,3}[-~]\d{1,3}|TV\+Movie|TV\+OVA', clean_title, re.I):
            if not re.search(r'[Ee](\d{1,3})|第\s?(\d{1,3})\s?[話话]', clean_title):
                return None
        patterns = [r'[Ee](\d{1,3})', r'第\s?(\d{1,3})\s?[話话]', r'\[(\d{1,3})(?:[vV]?\d?.*?|)\]', r'\s(\d{1,3})\s', r'-\s(\d{1,3})(?!\d)', r'(\d{1,3})\.mp4']
        for p in patterns:
            match = re.search(p, clean_title)
            if match: return int(match.group(1))
        return None

    ep_torrents = {}
    collection_torrents = []
    for t in torrents:
        # 過濾非中文資源
        if not is_chinese_resource(t.title):
            continue
            
        ep_num = extract_episode(t.title)
        if ep_num is not None:
            if ep_num not in ep_torrents: ep_torrents[ep_num] = []
            ep_torrents[ep_num].append(t)
        else:
            collection_torrents.append(t)
    
    sidebar_html = await shared.render_template_with_theme(
        "components/nyaa_sidebar.html",
        collection_torrents=collection_torrents,
        search_query=search_query,
        is_fallback=is_fallback
    )
    
    # 格式化 ep_torrents 以便 JSON 序列化 (Torrent 對象轉為 dict)
    serializable_ep_torrents = {}
    for ep_num, torrents in ep_torrents.items():
        serializable_ep_torrents[ep_num] = [
            {
                'title': t.title,
                'magnet': t.magnet_url,
                'seeders': t.seeders,
                'leechers': t.leechers,
                'size': t.size
            } for t in torrents
        ]

    return jsonify({
        'sidebar_html': sidebar_html,
        'ep_torrents': serializable_ep_torrents
    })



