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

import requests, re, json

from bs4 import BeautifulSoup

from shared import appconf

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
        for module in modules:
            m_type = module.get('module_type')
            if m_type == 'MODULE_TYPE_TITLE':
                arinfo['title'] = module.get('module_title', {}).get('text', '')
            elif m_type == 'MODULE_TYPE_AUTHOR':
                author = module.get('module_author', {})
                arinfo['author']['mid'] = author.get('mid')
                arinfo['author']['name'] = author.get('name')
                arinfo['author']['face'] = author.get('face')
                arinfo['publish_time'] = author.get('pub_ts', 0)
            elif m_type == 'MODULE_TYPE_STAT':
                stat = module.get('module_stat', {})
                arinfo['stats']['like'] = stat.get('like', {}).get('count', 0)
                arinfo['stats']['coin'] = stat.get('coin', {}).get('count', 0)
                arinfo['stats']['favorite'] = stat.get('favorite', {}).get('count', 0)
                arinfo['stats']['share'] = stat.get('forward', {}).get('count', 0) # forward is share?

        if not arinfo['title'] and 'basic' in detail:
            arinfo['title'] = detail['basic'].get('title', '').replace(' - 哔哩哔哩', '')
        
        if not arinfo['author']['mid'] and 'basic' in detail:
            arinfo['author']['mid'] = detail['basic'].get('uid', 0)

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
                modules = state.get('detail', {}).get('modules', [])
                content_html = ""
                for module in modules:
                    if module.get('module_type') == 'MODULE_TYPE_CONTENT':
                        paragraphs = module.get('module_content', {}).get('paragraphs', [])
                        for p in paragraphs:
                            p_type = p.get('para_type')
                            if p_type == 1: # Text
                                text_nodes = p.get('text', {}).get('nodes', [])
                                p_content = ""
                                for node in text_nodes:
                                    if node.get('type') == 'TEXT_NODE_TYPE_WORD':
                                        p_content += node.get('word', {}).get('words', '')
                                content_html += f"<p>{p_content}</p>"
                            elif p_type == 2: # Image
                                pics = p.get('pic', {}).get('pics', [])
                                for pic in pics:
                                    img_url = pic.get('url')
                                    if img_url:
                                        # Use proxy for images as per project convention
                                        proxied_url = '/proxy/pic/' + img_url.split('//')[1]
                                        content_html += f'<img src="{proxied_url}">'
                
                if content_html:
                    # Wrap in a div to match expected structure
                    return f'<div id="main-article">{content_html}</div>'
            except:
                pass
        return '<p>無法解析文章內容。</p>'

    article_body.attrs = {}
    article_body['id'] = 'main-article'

    del_elems = []
    purge_elems = []
    for child in article_body.descendants:
        if not child.name:
            continue

        if child.name.startswith('h'):
            child.name = f'h{int(child.name[1:])+1}'

        if child.name == 'strong' and child.parent.name.startswith('h'):
            purge_elems.append(child.parent)

        if not hasattr(child, 'attrs'):
            continue

        if child.name == 'a':
            if 'href' not in child:
                continue
            
            child['href'] = child['href'].split('//')[1].strip('www.bilibili.com')
            continue
        elif child.name == 'img':
            try:
                child['src'] = '/proxy/pic/' + child['data-src'].split('//')[1]
            except:
                pass
            continue
        elif child.name == 'span' and not child.parent in purge_elems:
            purge_elems.append(child.parent)
        
        child.attrs = {}

    for purge_elem in purge_elems:
        try:
            purge_elem.string = purge_elem.get_text()
        except:
            pass

    for del_elem in del_elems:
        try:
            del_elem.extract()
        except:
            pass

    return str(article_body)

'''Convert the article to any file.'''
def article_to_any(article_text, dest_fmt):
    cmd = ['pandoc', '-f', 'html', '-t', dest_fmt, '-']
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    output, _ = p.communicate(input=article_to_html(article_text).encode("utf-8"))
    return output.decode("utf-8")

async def video_get_src_for_qn(vi, idx, quality = 16):
    '''Get a specific available source for video.'''
    cid = await vi.get_cid(idx)
    api = Api('https://api.bilibili.com/x/player/playurl', 'GET',
              verify=(not not vi.credential.sessdata),
              credential=vi.credential)
    api.params={ 'avid': vi.get_aid(), 'cid': cid, 'qn': quality, 'platform': 'html5', 'high_quality': 1 }
    return await api.request()

async def video_get_dash_for_qn(vi, idx):
    '''Get a specific available source for video.'''
    cid = await vi.get_cid(idx)
    api = Api('https://api.bilibili.com/x/player/playurl', 'GET',
              verify=(not not vi.credential.sessdata),
              json_body=True,
              credential=vi.credential)
    api.params = { 'avid': vi.get_aid(), 'cid': cid, 'fnval': '16', 'platform': 'html5', 'high_quality': 1 }
    return await api.request()

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
    
