import asyncio
import httpx

async def test_disconnect():
    async with httpx.AsyncClient() as client:
        print("Visiting video page to cache URL...")
        await client.get("http://localhost:8000/video/BV1E6qQBqEos")
        
        print("Starting stream request...")
        try:
            async with client.stream("GET", "http://localhost:8000/proxy/video/BV1E6qQBqEos_0_16") as resp:
                print(f"Status: {resp.status_code}")
                count = 0
                async for chunk in resp.aiter_bytes():
                    count += len(chunk)
                    if count % (1024*1024) == 0:
                        print(f"Received {count // (1024*1024)} MB")
                    if count > 5 * 1024 * 1024: # 5MB
                        print("Simulating client disconnect...")
                        break
        except Exception as e:
            print(f"Error: {e}")
        print("Client disconnected.")

if __name__ == "__main__":
    asyncio.run(test_disconnect())