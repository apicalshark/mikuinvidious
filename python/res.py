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

from xml.dom import minidom

from bilibili_api import video
from danmaku import danmaku_xml_conv
from extra import bcc_to_vtt
from quart import jsonify, Response
from shared import Network, app


@app.route("/res/danmaku/<vid>:<idx>")
async def danmaku_res(vid, idx=0):
    # Check if this is a live room ID (all digits)
    if vid.isdigit():
        return jsonify([])

    from shared import appcred
    v = video.Video(bvid=vid, credential=appcred)
    try:
        xml = await v.get_danmaku_xml(int(idx))
        return jsonify(danmaku_xml_conv(minidom.parseString(xml)))
    except Exception as e:
        print(f"Danmaku error: {e}")
        return jsonify([])


@app.route("/res/subtitle/<vid>:<idx>/<lang>.vtt")
async def subtitle_res(vid, idx, lang):
    from shared import appcred, appredis
    
    cache_key = f"miku_vtt_{vid}_{idx}_{lang}"
    cached_vtt = await appredis.get(cache_key)
    if cached_vtt:
        return Response(cached_vtt, content_type="text/vtt")

    v = video.Video(bvid=vid, credential=appcred)
    try:
        # Get cid for the specific index
        cid = await v.get_cid(int(idx))
        
        # Fetch info with cid to get correct subtitles for that page
        from bilibili_api.utils.network import Api
        api_info = video.API["info"]["detail"]
        params = {"bvid": vid, "cid": cid}
        res = await Api(**api_info, credential=appcred).update_params(**params).result
        
        subtitle_list = res.get("subtitle", {}).get("list", [])
        target = next((s for s in subtitle_list if s["lan"] == lang), None)
        if not target:
            return "Subtitle not found", 404

        url = target["subtitle_url"]
        if url.startswith("//"):
            url = "https:" + url

        client = await Network.get_async_client()
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            return "Failed to fetch subtitle", 502

        vtt = bcc_to_vtt(resp.json())
        await appredis.setex(cache_key, 3600, vtt) # Cache for 1 hour
        return Response(vtt, content_type="text/vtt")
    except Exception as e:
        print(f"Subtitle error: {e}")
        return "Internal Error", 500
