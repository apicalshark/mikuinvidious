import asyncio
from urllib.parse import urljoin

from pyquery import PyQuery as pq
from shared import Network


class NyaaResult:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id")
        self.category = kwargs.get("category")
        self.title = kwargs.get("title")
        self.url = kwargs.get("url")
        self.torrent_url = kwargs.get("torrent_url")
        self.magnet_url = kwargs.get("magnet_url")
        self.size = kwargs.get("size")
        self.timestamp = kwargs.get("timestamp")
        self.seeders = kwargs.get("seeders")
        self.leechers = kwargs.get("leechers")
        self.downloads = kwargs.get("downloads")
        self.is_trusted = kwargs.get("is_trusted", False)
        self.is_remake = kwargs.get("is_remake", False)
        self.is_batch = kwargs.get("is_batch", False)

    def to_dict(self):
        return vars(self)


def _to_int(value) -> int:
    """Best-effort int() for scraped table cells. Upstream text may be
    empty, dashed, or comma-grouped ("1,234"); never raise."""
    try:
        return int(str(value if value is not None else 0).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0


async def search_nyaa(query: str, trusted_only: bool = True, max_pages: int = 7) -> list[NyaaResult]:
    """
    Search nyaa.si with automatic page detection and parallel fetching.
    """

    base_url = "https://nyaa.si/"
    client = await Network.get_async_client()

    async def fetch_html(page_num):
        params = {"f": "2" if trusted_only else "0", "c": "1_3", "q": query}
        if page_num > 1:
            params["p"] = page_num

        try:
            resp = await client.get(
                base_url,
                params=params,
                headers={
                    "Referer": "https://nyaa.si/",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,webp,image/apng,*/*;q=0.8",
                },
                timeout=15.0,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            print(f"[Nyaa] Page {page_num} fetch error: {e}")
            return None

    # 1. Fetch the first page to detect the total page count
    first_page_html = await fetch_html(1)
    if not first_page_html:
        return []

    doc_first = pq(first_page_html)

    # Parse the total page count
    # Nyaa's paginator usually lives in <ul class="pagination">
    # Find the last button that is numeric (not "»")
    total_pages = 1
    pagination = doc_first("ul.pagination li a")
    page_nums = []
    for p in pagination.items():
        txt = p.text().strip()
        if txt.isdigit():
            page_nums.append(int(txt))
    if page_nums:
        total_pages = max(page_nums)

    # Cap the page count to avoid overloading Nyaa or getting IP-banned
    target_pages = min(total_pages, max_pages)

    # 2. Fetch any remaining pages concurrently
    pages_content = [first_page_html]
    if target_pages > 1:
        remaining_pages = await asyncio.gather(*(fetch_html(p) for p in range(2, target_pages + 1)))
        pages_content.extend([html for html in remaining_pages if html])

    all_results = []
    for html in pages_content:
        doc = pq(html)
        rows = doc("table.torrent-list tbody tr")
        for row_node in rows.items():
            is_trusted = row_node.has_class("success")
            is_remake = row_node.has_class("danger")
            is_batch = row_node.has_class("warning")

            title_link = row_node("td:nth-child(2) a:last-child")
            title = title_link.attr("title") or title_link.text()
            detail_path = title_link.attr("href")
            item_id = detail_path.split("/")[-1] if detail_path else "0"

            download_td = row_node("td:nth-child(3)")
            torrent_url = urljoin(base_url, download_td('a[href$=".torrent"]').attr("href") or "")
            magnet_url = download_td('a[href^="magnet:"]').attr("href") or ""

            all_results.append(
                NyaaResult(
                    id=item_id,
                    title=title,
                    url=urljoin(base_url, detail_path or ""),
                    torrent_url=torrent_url,
                    magnet_url=magnet_url,
                    size=row_node("td:nth-child(4)").text().strip(),
                    timestamp=_to_int(row_node("td:nth-child(5)").attr("data-timestamp")),
                    seeders=_to_int(row_node("td:nth-child(6)").text()),
                    leechers=_to_int(row_node("td:nth-child(7)").text()),
                    downloads=_to_int(row_node("td:nth-child(8)").text()),
                    is_trusted=is_trusted,
                    is_remake=is_remake,
                    is_batch=is_batch,
                )
            )

    # Deduplicate by ID
    unique_results = []
    seen_ids = set()
    for res in all_results:
        if res.id not in seen_ids:
            unique_results.append(res)
            seen_ids.add(res.id)

    return unique_results
