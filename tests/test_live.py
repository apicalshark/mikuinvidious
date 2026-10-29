import json
import struct
import unittest

import brotli
from api.live import LiveDanmaku


class TestLive(unittest.TestCase):
    def setUp(self):
        self.ld = LiveDanmaku(123456)

    def test_unpack_heartbeat_response(self):
        # Header: len=20, hlen=16, protover=1, type=3, seq=1; view=12345
        header = struct.pack(">IHHII", 20, 16, 1, 3, 1)
        body = struct.pack(">I", 12345)
        raw = header + body

        unpacked = self.ld._unpack(raw)
        self.assertEqual(len(unpacked), 1)
        self.assertEqual(unpacked[0]["protocol_version"], 1)
        self.assertEqual(unpacked[0]["datapack_type"], 3)
        self.assertEqual(unpacked[0]["data"], {"view": 12345})

    def test_unpack_verify_success_response(self):
        payload = json.dumps({"code": 0, "msg": "ok"}).encode("utf-8")
        packet = struct.pack(">IHHII", 16 + len(payload), 16, 0, 8, 1) + payload

        unpacked = self.ld._unpack(packet)
        self.assertEqual(len(unpacked), 1)
        self.assertEqual(unpacked[0]["protocol_version"], 0)
        self.assertEqual(unpacked[0]["datapack_type"], 8)
        self.assertEqual(unpacked[0]["data"], {"code": 0, "msg": "ok"})

    def test_unpack_brotli_notice_packets(self):
        notice1 = json.dumps({"cmd": "DANMU_MSG", "info": ["hello"]}).encode("utf-8")
        pkt1 = struct.pack(">IHHII", 16 + len(notice1), 16, 0, 5, 1) + notice1
        notice2 = json.dumps({"cmd": "DANMU_MSG", "info": ["world"]}).encode("utf-8")
        pkt2 = struct.pack(">IHHII", 16 + len(notice2), 16, 0, 5, 1) + notice2

        compressed = brotli.compress(pkt1 + pkt2)
        outer = struct.pack(">IHHII", 16 + len(compressed), 16, 3, 5, 1) + compressed

        unpacked = self.ld._unpack(outer)
        self.assertEqual(len(unpacked), 2)
        self.assertEqual(unpacked[0]["data"]["cmd"], "DANMU_MSG")
        self.assertEqual(unpacked[0]["data"]["info"], ["hello"])
        self.assertEqual(unpacked[1]["data"]["cmd"], "DANMU_MSG")
        self.assertEqual(unpacked[1]["data"]["info"], ["world"])

    def test_unpack_truncated_or_corrupt(self):
        # Too short (< 16 bytes)
        self.assertEqual(self.ld._unpack(b"short"), [])
        # Corrupt brotli data
        bad_brotli = struct.pack(">IHHII", 30, 16, 3, 5, 1) + b"not_brotli_data!"
        # Should not raise exception
        res = self.ld._unpack(bad_brotli)
        self.assertIsInstance(res, list)


if __name__ == "__main__":
    unittest.main()
