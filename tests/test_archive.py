"""Behavioral tests that run without YouTube or Google Drive access."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from youtube_script_agent.download import caption_tracks, retry_seconds
from youtube_script_agent.manifest import export_index, summary
from youtube_script_agent.sources import Video, normalize_source
from youtube_script_agent.state import ArchiveState, next_start


class SourceTests(unittest.TestCase):
    def test_short_and_watch_addresses_resolve_to_same_video(self):
        video = Video("sBfp3Lp70Bc")
        self.assertEqual(normalize_source("https://youtu.be/sBfp3Lp70Bc?t=19"), (video.url, True))
        self.assertEqual(normalize_source("https://youtube.com/shorts/sBfp3Lp70Bc"), (video.url, True))
        self.assertEqual(normalize_source("https://youtube.com/@xpinman/shorts"), normalize_source("@xpinman"))
        with self.assertRaises(ValueError):
            normalize_source("https://youtube.com.example.org/@xpinman")


class CaptionTests(unittest.TestCase):
    def test_same_language_manual_and_asr_are_distinct(self):
        timedtext = "https://www.youtube.com/api/timedtext?lang=zh"
        manual = {"zh": [{"ext": "srt", "url": timedtext}]}
        asr = {"ext": "srt", "url": timedtext + "&kind=asr"}
        auto = {
            "zh": [asr, {"ext": "vtt", "url": "https://manifest.googlevideo.com/list", "protocol": "m3u8_native"}],
            "zh-orig": [asr],
            "en": [{"ext": "srt", "url": timedtext + "&tlang=en"}],
        }
        self.assertEqual(caption_tracks(manual, auto), {"zh.manual": manual["zh"], "zh.auto": [asr]})


class RateLimitTests(unittest.TestCase):
    def test_201st_attempt_waits_for_oldest_start_to_expire(self):
        history = list(range(0, 3600, 18))
        self.assertEqual(len(history), 200)
        self.assertEqual(next_start(3599, history), 3600)
        self.assertEqual(next_start(3600, history + [3600]), 3618)
        self.assertEqual(next_start(3600, history, blocked_until=4000), 4000)

    def test_attempt_spacing_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            initial = ArchiveState(folder)
            initial.reserve(clock=lambda: 100, sleep=lambda _: None)
            initial.db.close()
            reopened = ArchiveState(folder)
            times = iter([117, 118])
            waits = []
            reopened.reserve(clock=lambda: next(times), sleep=waits.append)
            self.assertEqual(waits, [1])
            with reopened.db:
                self.assertEqual(reopened.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 2)
            reopened.db.close()

    def test_pause_is_preserved_with_the_failed_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            state = ArchiveState(folder)
            state.add_source("channel", [Video("sBfp3Lp70Bc")])
            with patch("youtube_script_agent.state.time.time", return_value=100):
                pause = state.finish(state.videos("channel")[0], "rate_limited", "", [], retry_after=120)
            self.assertEqual(pause, 220)
            self.assertEqual(state.videos("channel")[0]["status"], "rate_limited")
            self.assertEqual(state.db.execute("SELECT until FROM limits").fetchone()[0], pause)
            state.db.close()

    def test_retry_after_uses_header_when_present(self):
        self.assertEqual(retry_seconds("120"), 120)
        self.assertEqual(retry_seconds("Wed, 25 Nov 2026 00:02:00 GMT", 1795564800), 120)
        self.assertEqual(retry_seconds(None), 900)


class ResumeTests(unittest.TestCase):
    def test_channel_and_single_video_share_verified_files(self):
        with tempfile.TemporaryDirectory() as folder:
            state = ArchiveState(folder)
            video = Video("sBfp3Lp70Bc")
            state.add_source("channel", [video])
            state.add_source("video", [video])
            track = {
                "video_id": video.id, "filename": "example.srt", "language": "zh",
                "kind": "manual", "sha256": "hash", "md5": "md5", "nbytes": 10,
            }
            entry = state.videos("channel")[0]
            state.finish(entry, "downloaded", "", [track])
            with self.assertRaises(ValueError):
                state.record_upload(video.id, "example.srt", "wrong-hash", "drive-private-id")
            state.record_upload(video.id, "example.srt", "hash", "drive-private-id")
            state.finish(entry, "downloaded", "", [track])
            self.assertEqual(summary(state, "video")["video_statuses"], {"archived": 1})
            self.assertEqual(state.tracks("channel", pending_only=True), [])
            index = Path(folder) / "index.csv"
            export_index(state, "video", index)
            self.assertNotIn("drive-private-id", index.read_text())
            self.assertIn(",zh,manual,archived", index.read_text())
            state.db.close()


if __name__ == "__main__":
    unittest.main()
