import unittest

from dash_mpd import generate_vod_mpd


class TestMpd(unittest.TestCase):
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

    def test_audio_tracks_sorted_descending_by_bandwidth(self):
        dash_data = {
            "dash": {
                "duration": 60,
                "minBufferTime": 1.5,
                "audio": [
                    {
                        "id": 30216,
                        "codecid": 0,
                        "bandwidth": 51101,
                        "codecs": "mp4a.40.5",
                        "SegmentBase": {"Initialization": "0-942", "indexRange": "943-1682"},
                    },
                    {
                        "id": 30280,
                        "codecid": 0,
                        "bandwidth": 320000,
                        "codecs": "mp4a.40.2",
                        "SegmentBase": {"Initialization": "0-800", "indexRange": "801-1200"},
                    },
                    {
                        "id": 30232,
                        "codecid": 0,
                        "bandwidth": 119790,
                        "codecs": "mp4a.40.2",
                        "SegmentBase": {"Initialization": "0-933", "indexRange": "934-1673"},
                    },
                ],
            }
        }
        mpd = generate_vod_mpd("BV1xx411c7xx", 0, dash_data)
        self.assertIsNotNone(mpd)
        pos_30280 = mpd.find("audio_30280_0")
        pos_30232 = mpd.find("audio_30232_0")
        pos_30216 = mpd.find("audio_30216_0")
        self.assertTrue(pos_30280 != -1 and pos_30232 != -1 and pos_30216 != -1)
        self.assertLess(pos_30280, pos_30232)
        self.assertLess(pos_30232, pos_30216)


if __name__ == "__main__":
    unittest.main()
