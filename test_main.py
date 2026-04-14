import tempfile
import unittest
from unittest import mock
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import main


class TestMainHelpers(unittest.TestCase):
    def test_build_payload_keeps_fields(self):
        payload = main.build_payload(
            prompt="p",
            model="m",
            images=["img"],
            enhance_prompt=False,
            enable_upsample=False,
            aspect_ratio="16:9",
        )
        self.assertEqual(payload["prompt"], "p")
        self.assertEqual(payload["model"], "m")
        self.assertEqual(payload["images"], ["img"])
        self.assertFalse(payload["enhance_prompt"])
        self.assertFalse(payload["enable_upsample"])
        self.assertEqual(payload["aspect_ratio"], "16:9")

    def test_pick_video_url_supports_multiple_shapes(self):
        self.assertEqual(main.pick_video_url({"video_url": "a"}), "a")
        self.assertEqual(main.pick_video_url({"url": "b"}), "b")
        self.assertEqual(main.pick_video_url({"output": {"video_url": "c"}}), "c")
        self.assertIsNone(main.pick_video_url({}))

    def test_merge_videos_fallback_to_reencode(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = f"{tmp}/final.mp4"
            p1 = f"{tmp}/a.mp4"
            p2 = f"{tmp}/b.mp4"
            with open(p1, "wb") as f1:
                f1.write(b"a")
            with open(p2, "wb") as f2:
                f2.write(b"b")

            with mock.patch("main.subprocess.run") as run_mock:
                run_mock.side_effect = [
                    main.subprocess.CalledProcessError(1, "ffmpeg"),
                    mock.Mock(),
                ]
                merged = main.merge_videos([p1, p2], out)
                self.assertEqual(merged, out)
                self.assertEqual(run_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
