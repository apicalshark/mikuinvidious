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
from flask import jsonify
from danmaku import danmaku_xml_conv
from xml.dom import minidom
from bilibili_api import video

from shared import *

@app.route('/res/danmaku/<vid>:<idx>')
async def danmaku_res(vid, idx=0):
    # Check if this is a live room ID (all digits)
    if vid.isdigit():
        return jsonify([])

    v = video.Video(bvid=vid)
    try:
        xml = await v.get_danmaku_xml(int(idx))
        return jsonify(danmaku_xml_conv(minidom.parseString(xml)))
    except Exception as e:
        print(f"Danmaku error: {e}")
        return jsonify([])
