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

from bilibili_api.utils.network_httpx import request
from bilibili_api.exceptions import ArgsException

from bs4 import BeautifulSoup

from shared import appconf

'''Format the result returned by cv link.'''
def article_to_html(article_text):
    article_soup = BeautifulSoup(article_text, features='lxml')
    article_body = article_soup.find('div', id='read-article-holder')
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
                if appconf['proxy']['image']:
                    child['src'] = '/proxy/pic/' + child['data-src'].split('//')[1]
                else:
                    child['src'] = child['data-src']
                    del child['data-src']
                    del child['data-size']
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
    return await request('GET', 'https://api.bilibili.com/x/player/playurl',
                         params={ 'avid': vi.get_aid(), 'cid': cid, 'qn': quality },
                         credential=vi.credential)

async def video_get_dash_for_qn(vi, idx):
    '''Get a specific available source for video.'''
    cid = await vi.get_cid(idx)
    return await request('GET', 'https://api.bilibili.com/x/player/playurl',
                         params={ 'avid': vi.get_aid(), 'cid': cid, 'fnval': '16' },
                         credential=vi.credential)

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
    
