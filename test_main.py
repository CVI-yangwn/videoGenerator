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

    def test_merge_videos_with_opencv_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = f"{tmp}/final.mp4"
            p1 = f"{tmp}/a.mp4"
            p2 = f"{tmp}/b.mp4"
            open(p1, "wb").close()
            open(p2, "wb").close()

            frame1 = mock.Mock()
            frame1.shape = (100, 200, 3)
            frame2 = mock.Mock()
            frame2.shape = (120, 240, 3)

            first_cap = mock.Mock()
            first_cap.isOpened.return_value = True
            first_cap.get.side_effect = [
                25.0,
                200,
                100,
            ]

            cap1 = mock.Mock()
            cap1.isOpened.return_value = True
            cap1.read.side_effect = [(True, frame1), (False, None)]

            cap2 = mock.Mock()
            cap2.isOpened.return_value = True
            cap2.read.side_effect = [(True, frame2), (False, None)]

            writer = mock.Mock()
            writer.isOpened.return_value = True

            with mock.patch("main.cv2.VideoCapture", side_effect=[first_cap, cap1, cap2]), \
                mock.patch("main.cv2.VideoWriter", return_value=writer), \
                mock.patch("main.cv2.VideoWriter_fourcc", return_value=1234), \
                mock.patch("main.cv2.resize", return_value=frame1) as resize_mock:
                merged = main.merge_videos([p1, p2], out)
                self.assertEqual(merged, out)
                self.assertEqual(writer.write.call_count, 2)
                resize_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
