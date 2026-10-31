import asyncio
import json
import os
import subprocess

import aiohttp

CHROME_BIN = "/usr/bin/chromium-browser"
PORT = 9222
URL = "https://space.bilibili.com/177291194"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "study_space_debug")
os.makedirs(OUTPUT_DIR, exist_ok=True)


async def run_cdp():
    # 1. Launch Chromium
    chrome_proc = subprocess.Popen(
        [
            CHROME_BIN,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            f"--remote-debugging-port={PORT}",
            "--disable-blink-features=AutomationControlled",
            f"--user-agent={UA}",
            "--window-size=1920,1080",
            "--lang=zh-CN",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        await asyncio.sleep(2)
        async with aiohttp.ClientSession() as session:
            # 2. Get or create new target
            async with session.put(f"http://127.0.0.1:{PORT}/json/new") as resp:
                target = await resp.json()
            ws_url = target["webSocketDebuggerUrl"]
            print(f"[CDP] Connected to target: {target['id']}")

            async with session.ws_connect(ws_url, max_msg_size=50 * 1024 * 1024) as ws:
                req_id = 0
                futures = {}

                async def send(method, params=None):
                    nonlocal req_id
                    req_id += 1
                    msg = {"id": req_id, "method": method, "params": params or {}}
                    fut = asyncio.get_event_loop().create_future()
                    futures[req_id] = fut
                    await ws.send_json(msg)
                    return await fut

                network_requests = {}
                api_responses = {}

                # Background listener
                async def listener():
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if "id" in data and data["id"] in futures:
                                futures[data["id"]].set_result(data.get("result", {}))
                            elif "method" in data:
                                method = data["method"]
                                params = data.get("params", {})
                                if method == "Network.requestWillBeSent":
                                    rid = params["requestId"]
                                    network_requests[rid] = {
                                        "requestId": rid,
                                        "url": params["request"]["url"],
                                        "method": params["request"]["method"],
                                        "headers": params["request"]["headers"],
                                        "postData": params["request"].get("postData"),
                                        "wallTime": params.get("wallTime"),
                                    }
                                elif method == "Network.responseReceived":
                                    rid = params["requestId"]
                                    if rid in network_requests:
                                        network_requests[rid]["status"] = params["response"]["status"]
                                        network_requests[rid]["statusText"] = params["response"]["statusText"]
                                        network_requests[rid]["responseHeaders"] = params["response"]["headers"]
                                        network_requests[rid]["mimeType"] = params["response"]["mimeType"]
                                elif method == "Network.loadingFinished":
                                    rid = params["requestId"]
                                    if rid in network_requests:
                                        network_requests[rid]["finished"] = True

                listener_task = asyncio.create_task(listener())

                # 3. Enable domains and anti-detection
                await send("Page.enable")
                await send("Network.enable")
                await send("Runtime.enable")

                stealth_js = """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                """
                await send("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js})

                print(f"[CDP] Navigating to {URL}...")
                await send("Page.navigate", {"url": URL})

                # Wait for page to load and network activity
                print("[CDP] Waiting 12 seconds for page & API calls...")
                await asyncio.sleep(6)

                # Scroll down to trigger lazy loading / archives
                await send("Runtime.evaluate", {"expression": "window.scrollTo(0, 1000);"})
                await asyncio.sleep(6)

                # Capture full DOM
                dom_res = await send("Runtime.evaluate", {"expression": "document.documentElement.outerHTML"})
                html = dom_res.get("result", {}).get("value", "")
                with open(f"{OUTPUT_DIR}/page.html", "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"[CDP] Saved HTML ({len(html)} bytes)")

                # Capture __INITIAL_STATE__
                init_res = await send(
                    "Runtime.evaluate", {"expression": "JSON.stringify(window.__INITIAL_STATE__ || null)"}
                )
                init_val = init_res.get("result", {}).get("value")
                if init_val:
                    with open(f"{OUTPUT_DIR}/initial_state.json", "w", encoding="utf-8") as f:
                        f.write(init_val)
                    print(f"[CDP] Saved __INITIAL_STATE__ ({len(init_val)} bytes)")

                # Capture screenshot
                shot_res = await send("Page.captureScreenshot", {"format": "png"})
                if "data" in shot_res:
                    import base64

                    with open(f"{OUTPUT_DIR}/screenshot.png", "wb") as f:
                        f.write(base64.b64decode(shot_res["data"]))
                    print("[CDP] Saved screenshot")

                # Fetch API response bodies
                for rid, req in network_requests.items():
                    url = req.get("url", "")
                    if any(k in url for k in ["/x/", "bilibili.com/x/", "api."]) and req.get("finished"):
                        try:
                            body_res = await asyncio.wait_for(
                                send("Network.getResponseBody", {"requestId": rid}), timeout=2.0
                            )
                            api_responses[url] = {"request": req, "body": body_res.get("body")}
                            print(f"[API Captured] {req.get('status')} {url[:100]}")
                        except Exception as e:
                            api_responses[url] = {"request": req, "error": str(e)}

                # Save all network requests and API responses
                with open(f"{OUTPUT_DIR}/all_requests.json", "w", encoding="utf-8") as f:
                    json.dump(network_requests, f, indent=2, ensure_ascii=False)
                with open(f"{OUTPUT_DIR}/api_responses.json", "w", encoding="utf-8") as f:
                    json.dump(api_responses, f, indent=2, ensure_ascii=False)

                listener_task.cancel()
    finally:
        chrome_proc.terminate()
        chrome_proc.wait()
        print("[CDP] Chromium process exited.")


if __name__ == "__main__":
    asyncio.run(run_cdp())
