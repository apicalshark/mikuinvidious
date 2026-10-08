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

from .client import Api, build_chrome_headers
from .credential import Credential
from .exceptions import ArgsException, is_risk_error
from .video import _get_dm_img_params

__all__ = ["User", "VideoOrder", "ArticleOrder"]

_SPACE_ORIGIN = "https://space.bilibili.com"


def _space_headers(mid) -> dict:
    """Browser-exact headers for space pages (origin + profile referer)."""
    return build_chrome_headers(
        origin=_SPACE_ORIGIN, referer=f"{_SPACE_ORIGIN}/{mid}"
    )


class VideoOrder(Enum):
    """Video submission sort order."""

    PUBDATE = "pubdate"
    CLICK = "click"
    STOW = "stow"


class ArticleOrder(Enum):
    """Article sort order."""

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
                raise ArgsException("uid must be greater than 0")
            self.uid = uid
        elif name is not None:
            self.name = name
            self.uid = None
        else:
            raise ArgsException("One of uid and name must be provided")
        self.credential = credential if credential is not None else Credential()
        # Set when any fetch below hits risk control (see is_risk_error).
        # Views read it to tell "throttled, content missing" apart from a
        # genuinely empty channel. Per-request instances only — never cached.
        self._degraded = False
        # Preserve swallowed video-fetch failures for the neutral empty state.
        self._videos_load_failed = False

    async def get_user_info(self) -> dict:
        # Space endpoints are the most risk-controlled read surface (dm_img
        # fingerprint required; 412/-352 from datacenter IPs). Param set is
        # bundle-exact (fresh-space index-*.js: `{mid, token, platform: "web",
        # web_location: 1550101}` + RISK_USER_LOG dm_img_*).
        params = {"mid": self.uid, "token": "", "platform": "web", "web_location": 1550101}
        params.update(_get_dm_img_params(fingerprint=True))
        api = {
            "url": "https://api.bilibili.com/x/space/wbi/acc/info",
            "method": "GET",
            "verify": False,
            "headers": _space_headers(self.uid),
            "curl": True,
        }
        try:
            result = await Api(**api, credential=self.credential, wbi=True).update_params(**params).result
            if isinstance(result, dict) and (result.get("name") or result.get("face")):
                return result
            # Empty / risk-controlled result (e.g. v_voucher gate) -> fall through
        except Exception as e:
            if is_risk_error(e):
                self._degraded = True
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
                result = (
                    await Api(**card_api, credential=self.credential, wbi=False)
                    .update_params(photo=True, mid=self.uid)
                    .result
                )
                result = result if isinstance(result, dict) else {}
                card = result.get("card")
                if isinstance(card, dict):
                    card = dict(card)
                    card.setdefault("mid", str(self.uid))
                    return card
            except Exception as e:
                last = e
                if is_risk_error(e):
                    self._degraded = True
        if last is not None:
            raise last
        return {}

    async def get_videos(self, tid=0, pn=1, ps=30, keyword="", order=VideoOrder.PUBDATE) -> dict:
        self._videos_load_failed = False
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
        # Bundle-exact (fresh-space video tab): e1({pn, ps, tid, special_type,
        # order, mid, index: 0, keyword}) + {order_avoided: "true" (STRING —
        # bool True encodes as 1 and mismatches), platform: "web",
        # web_location: 333.1387 (tab spm)} + dm_img_*.
        params = {
            "mid": self.uid,
            "ps": ps,
            "tid": tid,
            "pn": pn,
            "keyword": keyword,
            "order": order,
            "order_avoided": "true",
            "platform": "web",
            "web_location": 333.1387,
            "special_type": "",
            "index": 0,
        }
        params.update(_get_dm_img_params(fingerprint=True))
        api = {
            "url": "https://api.bilibili.com/x/space/wbi/arc/search",
            "method": "GET",
            "verify": False,
            "headers": _space_headers(self.uid),
            "curl": True,
        }
        try:
            result = await Api(**api, credential=self.credential, wbi=True).update_params(**params).result
            if isinstance(result, dict) and result.get("list", {}).get("vlist"):
                return result
            # Empty / risk-controlled result (e.g. v_voucher gate) -> fall through
        except Exception as e:
            if is_risk_error(e):
                self._degraded = True
            else:
                self._videos_load_failed = True
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
        for _ in range(2):
            # Fresh device fingerprint (dm_img_*) per attempt, mirroring PipePipe's
            # regenerate-device-on-risk-control retry strategy. Param names are
            # bundle-exact: the key is `orderby`, not `order`.
            params = {
                "mid": self.uid,
                "keywords": "",
                "orderby": "pubdate",
                "pn": pn,
                "ps": ps,
            }
            params.update(_get_dm_img_params(fingerprint=True))
            try:
                data = await Api(**api, credential=self.credential, wbi=True).update_params(**params).result
                break
            except Exception as e:
                if is_risk_error(e):
                    self._degraded = True
                else:
                    self._videos_load_failed = True
        data = data if isinstance(data, dict) else {}
        archives = data.get("archives", []) if isinstance(data.get("archives"), list) else []
        vlist = []
        for a in archives:
            if not isinstance(a, dict):
                continue
            stat = a.get("stat") or {}
            vlist.append(
                {
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
                }
            )
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
        params.update(_get_dm_img_params(fingerprint=True))
        api = {
            "url": "https://api.bilibili.com/x/space/wbi/article",
            "method": "GET",
            "verify": False,
            "headers": _space_headers(self.uid),
            "curl": True,
        }
        try:
            return await Api(**api, credential=self.credential, wbi=True).update_params(**params).result
        except Exception as e:
            if is_risk_error(e):
                self._degraded = True
            raise
