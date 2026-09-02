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
"""
Bilibili user module.

Minimal drop-in for ``bilibili_api.user`` covering get_user_info,
get_videos and get_articles as used by MikuInvidious.
"""

from enum import Enum

from .client import Api
from .credential import Credential
from .exceptions import ArgsException
from .video import _get_dm_img_params

__all__ = ["User", "VideoOrder", "ArticleOrder"]


class VideoOrder(Enum):
    """投稿排序方式。"""
    PUBDATE = "pubdate"
    CLICK = "click"
    STOW = "stow"


class ArticleOrder(Enum):
    """专栏排序方式。"""
    PUBDATE = "publish_time"
    FAVORITE = "favorite"
    VIEW = "view"


class User:
    def __init__(self, uid=None, name=None, credential=None):
        if uid is not None:
            try:
                uid = int(uid)
            except (TypeError, ValueError):
                pass
            if uid <= 0:
                raise ArgsException("uid 不能小于或等于 0")
            self.uid = uid
        elif name is not None:
            self.name = name
            self.uid = None
        else:
            raise ArgsException("uid 和 name 必须提供一个")
        self.credential = credential if credential is not None else Credential()

    async def get_user_info(self) -> dict:
        params = {"mid": self.uid}
        api = {
            "url": "https://api.bilibili.com/x/space/wbi/acc/info",
            "method": "GET",
            "verify": False,
        }
        try:
            result = await Api(**api, credential=self.credential, wbi=True).update_params(**params).result
            if isinstance(result, dict) and (result.get("name") or result.get("face")):
                return result
            # Empty / risk-controlled result (e.g. v_voucher gate) -> fall through
        except Exception:
            pass
        # /x/space/wbi/acc/info is often risk-controlled / IP-blocked (412/-352)
        # from datacenter IPs. PipePipe uses the non-wbi /x/web-interface/card
        # endpoint instead, which serves the same profile fields and is not
        # IP-gated. Normalize its data.card into the space.html shape.
        card_api = {
            "url": "https://api.bilibili.com/x/web-interface/card",
            "method": "GET",
            "verify": False,
        }
        last = None
        for _ in range(2):
            try:
                result = await Api(**card_api, credential=self.credential, wbi=False).update_params(photo=True, mid=self.uid).result
                result = result if isinstance(result, dict) else {}
                card = result.get("card")
                if isinstance(card, dict):
                    card = dict(card)
                    card.setdefault("mid", str(self.uid))
                    return card
            except Exception as e:
                last = e
        if last is not None:
            raise last
        return {}

    async def get_videos(self, tid=0, pn=1, ps=30, keyword="", order=VideoOrder.PUBDATE) -> dict:
        if isinstance(order, VideoOrder):
            order = order.value
        try:
            pn = int(pn)
        except (TypeError, ValueError):
            pn = 1
        try:
            ps = int(ps)
        except (TypeError, ValueError):
            ps = 30
        pn = max(pn, 1)
        ps = max(ps, 1)
        params = {
            "mid": self.uid,
            "ps": ps,
            "tid": tid,
            "pn": pn,
            "keyword": keyword,
            "order": order,
            "order_avoided": True,
            "platform": "web",
            "web_location": 1550101,
        }
        api = {
            "url": "https://api.bilibili.com/x/space/wbi/arc/search",
            "method": "GET",
            "verify": False,
        }
        try:
            result = await Api(**api, credential=self.credential, wbi=True).update_params(**params).result
            if isinstance(result, dict) and result.get("list", {}).get("vlist"):
                return result
            # Empty / risk-controlled result (e.g. v_voucher gate) -> fall through
        except Exception:
            pass
        # The /x/space/wbi/arc/search endpoint is frequently risk-controlled /
        # IP-blocked (HTTP 412 / -352) from datacenter IPs without the WARP
        # proxy. Fall back to the /x/series/recArchivesByKeywords endpoint used
        # by PipePipe, which serves the same uploads and is not IP-gated.
        return await self.get_videos_fallback(pn=pn, ps=ps)

    async def get_videos_fallback(self, pn=1, ps=20) -> dict:
        """Fetch a user's uploads via the (web) recArchivesByKeywords endpoint.

        Falls back to this when ``arc/search`` is blocked. Normalizes the
        ``data.archives[]`` response into the ``data.list.vlist`` shape the rest
        of the app expects (space.html / space_json_feed).
        """
        api = {
            "url": "https://api.bilibili.com/x/series/recArchivesByKeywords",
            "method": "GET",
            "verify": False,
        }
        data = {}
        last_err = None
        for _ in range(2):
            # Fresh device fingerprint (dm_img_*) per attempt, mirroring PipePipe's
            # regenerate-device-on-risk-control retry strategy.
            params = {
                "mid": self.uid,
                "keywords": "",
                "order": "pubdate",
                "pn": pn,
                "ps": ps,
            }
            params.update(_get_dm_img_params())
            try:
                data = await Api(**api, credential=self.credential, wbi=True).update_params(**params).result
                break
            except Exception as e:
                last_err = e
        data = data if isinstance(data, dict) else {}
        archives = data.get("archives", []) if isinstance(data.get("archives"), list) else []
        vlist = []
        for a in archives:
            if not isinstance(a, dict):
                continue
            stat = a.get("stat") or {}
            vlist.append({
                "aid": a.get("aid"),
                "bvid": a.get("bvid", ""),
                "title": a.get("title", ""),
                "pic": (a.get("pic") or "").replace("http:", "https:"),
                "duration": a.get("duration", 0),
                "created": a.get("pubdate", a.get("ctime", 0)),
                "play": stat.get("view", 0),
                "comment": stat.get("reply", 0),
                "mid": self.uid,
                "author": a.get("author", "") or "",
                "desc": a.get("desc", ""),
            })
        # Match the shape returned by Api.result for the primary arc/search endpoint
        # (which unwraps to the data node): {list: {vlist}, page: {...}}.
        # The recArchivesByKeywords response exposes the real total under
        # data.page.total (mirroring arc/search's page.count) so pagination works.
        page_info = data.get("page") or {}
        try:
            total = int(page_info.get("total") or len(vlist))
        except (TypeError, ValueError):
            total = len(vlist)
        return {
            "list": {"vlist": vlist},
            "page": {"count": total, "pn": pn, "ps": ps},
        }

    async def get_articles(self, pn=1, order=ArticleOrder.PUBDATE, ps=30) -> dict:
        if isinstance(order, ArticleOrder):
            order = order.value
        params = {"mid": self.uid, "ps": ps, "pn": pn, "sort": order}
        api = {
            "url": "https://api.bilibili.com/x/space/wbi/article",
            "method": "GET",
            "verify": False,
        }
        return await Api(**api, credential=self.credential, wbi=True).update_params(**params).result
