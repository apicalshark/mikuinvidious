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

'''Bilibili extra apis'''

import subprocess

from bilibili_api.utils.network import Api
from bilibili_api.exceptions import ArgsException

import re, json

from bs4 import BeautifulSoup

from shared import appconf, translate_text

def get_article_info(article_text, cid):
    '''Extract article info from INITIAL_STATE in HTML.'''
    pattern = re.compile(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', re.DOTALL)
    match = pattern.search(article_text)
    
    arinfo = {
        'title': '',
        'author': {'mid': 0, 'face': '', 'name': ''},
        'publish_time': 0,
        'stats': {'view': 0, 'like': 0, 'coin': 0, 'favorite': 0, 'share': 0}
    }

    if match:
        state = json.loads(match.group(1))
        detail = state.get('detail', {})
        
        # Try to find modules
        modules = detail.get('modules', [])
        if not modules and 'item' in detail:
            modules = detail['item'].get('modules', [])
            
        for module in modules:
            m_type = module.get('module_type')
            
            # Title
            if m_type == 'MODULE_TYPE_TITLE' or module.get('module_title'):
                title_obj = module.get('module_title', {})
                arinfo['title'] = title_obj.get('text', '')
            
            # Author
            elif m_type == 'MODULE_TYPE_AUTHOR' or module.get('module_author'):
                author = module.get('module_author', {})
                arinfo['author']['mid'] = author.get('mid')
                arinfo['author']['name'] = author.get('name')
                arinfo['author']['face'] = author.get('face')
                arinfo['publish_time'] = author.get('pub_ts', 0)
            
            # Stats
            elif m_type == 'MODULE_TYPE_STAT' or module.get('module_stat'):
                stat = module.get('module_stat', {})
                arinfo['stats']['like'] = stat.get('like', {}).get('count', 0)
                arinfo['stats']['coin'] = stat.get('coin', {}).get('count', 0)
                arinfo['stats']['favorite'] = stat.get('favorite', {}).get('count', 0)
                arinfo['stats']['share'] = stat.get('forward', {}).get('count', 0)
                if 'view' in stat:
                    arinfo['stats']['view'] = stat.get('view', {}).get('count', 0)

        if not arinfo['title'] and 'basic' in detail:
            arinfo['title'] = detail['basic'].get('title', '').replace(' - 哔哩哔哩', '')
        
        if not arinfo['author']['mid'] and 'basic' in detail:
            arinfo['author']['mid'] = detail['basic'].get('uid', 0)
            
        if not arinfo['stats']['view'] and 'basic' in detail:
            arinfo['stats']['view'] = detail['basic'].get('view_count', 0)

    return arinfo

'''Format the result returned by cv link.'''
def article_to_html(article_text):
    article_soup = BeautifulSoup(article_text, features='lxml')
    article_body = article_soup.find('div', id='read-article-holder')
    
    if not article_body:
        # Try to parse from INITIAL_STATE if it's a new format article
        pattern = re.compile(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', re.DOTALL)
        match = pattern.search(article_text)
        if match:
            try:
                state = json.loads(match.group(1))
                detail = state.get('detail', {})
                modules = detail.get('modules', [])
                if not modules and 'item' in detail:
                    modules = detail['item'].get('modules', [])
                
                content_html = ""
                for module in modules:
                    if module.get('module_type') == 'MODULE_TYPE_CONTENT' or module.get('module_content'):
                        paragraphs = module.get('module_content', {}).get('paragraphs', [])
                        for p in paragraphs:
                            p_type = p.get('para_type')
                            if p_type == 1: # Text
                                text_nodes = p.get('text', {}).get('nodes', [])
                                p_content = ""
                                for node in text_nodes:
                                    text = ""
                                    url = None
                                    is_bold = False
                                    is_italic = False
                                    color = None
                                    
                                    if node.get('rich'):
                                        rich = node['rich']
                                        text = rich.get('text', '')
                                        url = rich.get('jump_url')
                                        if rich.get('emoji'):
                                            emoji_url = rich['emoji'].get('icon_url')
                                            if emoji_url:
                                                if emoji_url.startswith('//'):
                                                    emoji_url = 'https:' + emoji_url
                                                proxied_emoji = '/proxy/pic/' + emoji_url.split('//')[1]
                                                text = f'<img src="{proxied_emoji}" style="width: 1.2em; height: 1.2em; display: inline-block; vertical-align: middle;" alt="{text}">'
                                    elif node.get('word'):
                                        word = node['word']
                                        text = word.get('words', '')
                                        if word.get('style'):
                                            is_bold = word['style'].get('bold')
                                            is_italic = word['style'].get('italic')
                                            color = word['style'].get('color')
                                    
                                    # Fallback for older format if necessary
                                    if not text and node.get('type') == 'TEXT_NODE_TYPE_WORD':
                                        word = node.get('word', {})
                                        text = word.get('words', '')
                                        is_bold = word.get('bold')
                                        is_italic = word.get('italic')
                                        color = word.get('color')
                                    elif not text and node.get('type') == 'TEXT_NODE_TYPE_HYPERLINK':
                                        word = node.get('word', {})
                                        text = word.get('words', '')
                                        url = node.get('link', {}).get('url')

                                    if is_bold:
                                        text = f"<strong>{text}</strong>"
                                    if is_italic:
                                        text = f"<em>{text}</em>"
                                    if color:
                                        if not color.startswith('#'):
                                            color = f"#{color}"
                                        text = f'<span style="color: {color};">{text}</span>'
                                    
                                    if url:
                                        text = f'<a href="{url}">{text}</a>'
                                    
                                    p_content += text
                                
                                # Alignment
                                align_style = ""
                                align = p.get('align')
                                if align == 2 or align == 1: # Center (opus uses 1 for center sometimes?)
                                    align_style = ' style="text-align: center;"'
                                elif align == 3: # Right
                                    align_style = ' style="text-align: right;"'
                                    
                                content_html += f"<p{align_style}>{p_content}</p>"
                            elif p_type == 2: # Image
                                pics = p.get('pic', {}).get('pics', [])
                                for pic in pics:
                                    img_url = pic.get('url')
                                    if img_url:
                                        proxied_url = '/proxy/pic/' + img_url.split('//')[1]
                                        content_html += f'<figure style="text-align: center;"><img src="{proxied_url}" class="mx-auto"></figure>'
                            elif p_type == 7: # Code
                                code = p.get('code', {})
                                lang = code.get('lang', '').replace('language-', '')
                                content = code.get('content', '')
                                # Use html.escape if available, or just a simple replacement
                                import html as html_lib
                                content = html_lib.escape(content)
                                content_html += f'<pre><code class="language-{lang}">{content}</code></pre>'
                
                if content_html:
                    return translate_text(f'<div id="main-article">{content_html}</div>')
            except Exception as e:
                print(f"Error parsing opus INITIAL_STATE: {e}")
                pass
        return '<p>無法解析文章內容。</p>'

    article_body.attrs = {}
    article_body['id'] = 'main-article'

    for child in article_body.find_all(True): # find_all(True) gets all tags
        # Handle headers
        if child.name.startswith('h') and len(child.name) == 2:
            try:
                level = int(child.name[1:])
                child.name = f'h{min(level + 1, 6)}'
            except ValueError:
                pass

        # Handle images
        if child.name == 'img':
            if child.has_attr('data-src'):
                child['src'] = '/proxy/pic/' + child['data-src'].split('//')[1]
            elif child.has_attr('src') and child['src'].startswith('//'):
                child['src'] = '/proxy/pic/' + child['src'].split('//')[1]
            
            # Remove all other attributes except src and add mx-auto class
            src = child.get('src', '')
            child.attrs = {'src': src, 'class': 'mx-auto'}
            continue

        # Handle links
        if child.name == 'a':
            if child.has_attr('href'):
                href = child['href']
                if 'bilibili.com' in href:
                    # Try to make it relative if it's a bilibili link
                    href = href.split('bilibili.com')[-1]
                child['href'] = href
            # Keep only href
            href = child.get('href', '#')
            child.attrs = {'href': href}
            continue

        # Preserve some styles like alignment and color
        new_style = []
        if child.has_attr('style'):
            style = child['style']
            # Preserve text-align
            match_align = re.search(r'text-align\s*:\s*([^;]+)', style)
            if match_align:
                new_style.append(f'text-align: {match_align.group(1).strip()}')
            
            # Preserve color
            match_color = re.search(r'color\s*:\s*([^;]+)', style)
            if match_color:
                new_style.append(f'color: {match_color.group(1).strip()}')

            # Preserve font-weight
            match_weight = re.search(r'font-weight\s*:\s*([^;]+)', style)
            if match_weight:
                new_style.append(f'font-weight: {match_weight.group(1).strip()}')
        
        new_attrs = {}
        if new_style:
            new_attrs['style'] = '; '.join(new_style) + ';'
        
        # Keep classes for some elements if they look useful, but mostly clear them
        # Bilibili uses a lot of specific classes for layout.
        
        child.attrs = new_attrs

    return translate_text(str(article_body))

'''Convert the article to any file.'''
async def article_to_any(article_text, dest_fmt):
    cmd = ['pandoc', '-f', 'html', '-t', dest_fmt, '-']
    p = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await p.communicate(input=article_to_html(article_text).encode("utf-8"))
    return stdout.decode("utf-8")

async def video_get_src_for_qn(vi, idx, quality = 16):
    '''Get a specific available source for video.'''
    cid = await vi.get_cid(idx)
    api = Api('https://api.bilibili.com/x/player/playurl', 'GET',
              verify=(not not vi.credential.sessdata),
              credential=vi.credential)
    api.params={ 'avid': vi.get_aid(), 'cid': cid, 'qn': quality, 'platform': 'html5', 'high_quality': 1 }
    res = await api.request()

    # Prioritize Akamai mirrors for better direct access compatibility
    if 'durl' in res and res['durl']:
        for durl in res['durl']:
            urls = ([durl.get('url')] if durl.get('url') else []) + (durl.get('backup_url') or [])
            for u in urls:
                if u and '-mirrorakam.akamaized.net' in u:
                    durl['url'] = u
                    break
    return res

async def video_get_dash_for_qn(vi, idx):
    '''Get a specific available source for video.'''
    cid = await vi.get_cid(idx)
    api = Api('https://api.bilibili.com/x/player/playurl', 'GET',
              verify=(not not vi.credential.sessdata),
              json_body=True,
              credential=vi.credential)
    api.params = { 'avid': vi.get_aid(), 'cid': cid, 'fnval': '16', 'platform': 'html5', 'high_quality': 1 }
    res = await api.request()

    # Prioritize Akamai mirrors in DASH manifest
    if 'dash' in res and res['dash']:
        for media_type in ['video', 'audio']:
            if media_type in res['dash'] and res['dash'][media_type]:
                for item in res['dash'][media_type]:
                    urls = ([item.get('baseUrl')] if item.get('baseUrl') else []) + (item.get('backupUrl') or [])
                    for u in urls:
                        if u and '-mirrorakam.akamaized.net' in u:
                            item['baseUrl'] = u
                            break
    return res

# The following algorithm is adopted from bilibili-API-collect.
# https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/other/bvid_desc.md

table = 'fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF'
itable = { table[i]: i for i in range(len(table)) }

s = [11, 10, 3, 8, 4, 6]
XOR = 177451812
ADD = 8728348608

def bv2av(x):
    r = 0
    for i in range(6):
        r += itable[x[s[i]]] * 58 ** i
    return (r - ADD) ^ XOR

def av2bv(x):
    try:
        x = int(x[2:] if str(x).startswith('av') else x)
        x = (x ^ XOR) + ADD
        r = list('BV1  4 1 7  ')
        for i in range(6):
            r[s[i]] = table[x // 58 ** i % 58]
        return ''. join(r)
    except ValueError:
        raise ArgsException("avid 提供错误，必须是以 av 开头的数字组成的字符串。")
    
