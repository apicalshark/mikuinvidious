import unittest

import dash_mpd
from dash_mpd import generate_vod_mpd

try:
    import miku_core_rs
except ModuleNotFoundError as exc:
    if exc.name != "miku_core_rs":
        raise
    miku_core_rs = None


class TestMpdRs(unittest.TestCase):
    def setUp(self):
        self.sample_dash = {
            "dash": {
                "duration": 120,
                "minBufferTime": 1.5,
                "video": [
                    {
                        "id": 80,
                        "codecid": 7,
                        "bandwidth": 1500000,
                        "width": 1920,
                        "height": 1080,
                        "frameRate": "30",
                        "codecs": "avc1.640028",
                        "SegmentBase": {
                            "Initialization": "0-999",
                            "indexRange": "1000-1500",
                        },
                    },
                    {
                        "id": 80,
                        "codecid": 12,
                        "bandwidth": 1200000,
                        "width": 1920,
                        "height": 1080,
                        "frame_rate": "30",
                        "codecs": "hev1.1.6.L150.90",
                        "SegmentBase": {
                            "initialization": {"range": "0-1024"},
                            "indexRange": "1025-1600",
                        },
                    },
                ],
                "audio": [
                    {
                        "id": 30280,
                        "codecid": 0,
                        "bandwidth": 320000,
                        "codecs": "mp4a.40.2",
                        "SegmentBase": {
                            "Initialization": "0-800",
                            "indexRange": "801-1200",
                        },
                    }
                ],
            }
        }

    def test_mpd_generation_structure(self):
        mpd = generate_vod_mpd("BV1xx411c7xx", 0, self.sample_dash)
        self.assertIsNotNone(mpd)
        self.assertIn('<?xml version="1.0" encoding="UTF-8"?>', mpd)
        self.assertIn('mediaPresentationDuration="PT120S"', mpd)
        self.assertIn('minBufferTime="PT1.5S"', mpd)
        # 2 video adaptation sets (codecid 7 and 12) + 1 audio adaptation set
        self.assertIn('<AdaptationSet id="1" contentType="video"', mpd)
        self.assertIn('<AdaptationSet id="2" contentType="video"', mpd)
        self.assertIn('<AdaptationSet id="3" contentType="audio"', mpd)
        self.assertIn('<BaseURL>/proxy/dash/BV1xx411c7xx/0/video/80/7</BaseURL>', mpd)
        self.assertIn('<BaseURL>/proxy/dash/BV1xx411c7xx/0/video/80/12</BaseURL>', mpd)
        self.assertIn('<BaseURL>/proxy/dash/BV1xx411c7xx/0/audio/30280/0</BaseURL>', mpd)
        self.assertIn('<Initialization range="0-999"/>', mpd)
        self.assertIn('<Initialization range="0-1024"/>', mpd)
        self.assertIn('<Initialization range="0-800"/>', mpd)

    def test_empty_or_invalid_dash(self):
        self.assertIsNone(generate_vod_mpd("BV1xx411c7xx", 0, None))
        self.assertIsNone(generate_vod_mpd("BV1xx411c7xx", 0, {}))
        self.assertIsNone(generate_vod_mpd("BV1xx411c7xx", 0, {"dash": {}}))

    @unittest.skipIf(miku_core_rs is None, "miku_core_rs is not installed")
    def test_rust_mpd_direct_parity(self):
        self.assertTrue(hasattr(miku_core_rs, "generate_vod_mpd"))
        py_result = dash_mpd._generate_vod_mpd_py("BV1xx411c7xx", 0, self.sample_dash)
        rs_result = miku_core_rs.generate_vod_mpd("BV1xx411c7xx", 0, self.sample_dash)
        self.assertEqual(py_result, rs_result)


if __name__ == "__main__":
    unittest.main()
