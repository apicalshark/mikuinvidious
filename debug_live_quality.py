import asyncio
from bilibili_api import live
import json

async def main():
    try:
        room_id = 11365090 
        room = live.LiveRoom(room_id)
        play_data = await room.get_room_play_info_v2(
            live_protocol=live.LiveProtocol.FLV,
            live_format=live.LiveFormat.FLV
        )
        
        desc_list = play_data.get('play_url', {}).get('g_qn_desc', [])
        print("Available Qualities (qn):")
        for d in desc_list:
            print(f" - {d['desc']}: qn={d['qn']}")
            
        # Check if we can get a specific quality
        if desc_list:
            target_qn = desc_list[-1]['qn'] # lowest
            print(f"\nFetching play info for qn={target_qn}...")
            q_data = await room.get_room_play_info_v2(
                live_protocol=live.LiveProtocol.FLV,
                live_format=live.LiveFormat.FLV,
                live_qn=target_qn
            )
            # Find the url
            stream = q_data.get('play_url', {}).get('stream', [])
            for s in stream:
                for f in s.get('format', []):
                    for c in f.get('codec', []):
                        url = c.get('url') or c.get('base_url')
                        if url:
                            print(f"Success! URL found for qn={target_qn}")
                            return

    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    asyncio.run(main())
