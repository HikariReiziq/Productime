import time
import unittest

import _bootstrap  # noqa: F401

from gi.repository import GLib

from productime.domain.models import Sound
from productime.services.audio_service import AudioService, LoopPlayer, SoundLibrary
from support import ASSETS, TempData, make_wav, quiet_pygobject_asyncio_warning

# A real pipeline that decodes in real time but sends samples nowhere, so tests stay silent.
SILENT_SINK = "fakesink sync=true"


def setUpModule():
    quiet_pygobject_asyncio_warning()


def pump_until(predicate, timeout=6.0):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while context.iteration(False):
            pass
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class LoopPlayerTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.addCleanup(self.data.close)
        self.clip = make_wav(self.data.root / "clip.wav", seconds=0.3)
        self.player = LoopPlayer(SILENT_SINK)
        self.addCleanup(self.player.stop)

    def test_looping_clip_restarts_again_and_again_until_stopped(self):
        self.player.play(self.clip, loop=True, volume=1.0)

        self.assertTrue(pump_until(lambda: self.player.loop_count >= 3), self.player.loop_count)
        self.assertEqual(self.player.state, "playing")

    def test_stop_releases_the_pipeline(self):
        self.player.play(self.clip, loop=True, volume=1.0)
        pump_until(lambda: self.player.state == "playing")

        self.player.stop()

        self.assertEqual(self.player.state, "null")
        self.assertFalse(self.player.has_pipeline)

    def test_single_play_finishes_once_and_releases_the_pipeline(self):
        finished = []

        self.player.play(self.clip, loop=False, volume=1.0, on_finished=lambda: finished.append(True))

        self.assertTrue(pump_until(lambda: finished))
        self.assertEqual((self.player.state, self.player.loop_count), ("null", 0))

    def test_unplayable_file_reports_an_error_and_releases_the_pipeline(self):
        broken = self.data.root / "broken.wav"
        broken.write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunk" * 4)
        errors = []

        self.player.play(broken, loop=True, volume=1.0, on_error=errors.append)

        self.assertTrue(pump_until(lambda: errors))
        self.assertFalse(self.player.has_pipeline)


class AudioServiceTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.addCleanup(self.data.close)
        self.library = SoundLibrary(self.data.repo, ASSETS, self.data.data_dir / "sounds")
        self.audio = AudioService(self.library, audio_sink=SILENT_SINK)
        self.addCleanup(self.audio.shutdown)

    def test_idle_service_holds_no_pipeline(self):
        self.assertEqual((self.audio.alert_state(), self.audio.preview_state()), ("null", "null"))

    def test_alert_plays_a_bundled_sound_until_stopped(self):
        self.audio.start_alert("bundled-community")

        self.assertTrue(pump_until(lambda: self.audio.alert_state() == "playing"))
        self.assertEqual(self.audio.alert_sound_id, "bundled-community")
        self.audio.stop_alert()

        self.assertEqual(self.audio.alert_state(), "null")
        self.assertIsNone(self.audio.alert_sound_id)

    def test_custom_sound_loops_as_an_alert(self):
        sound = self.library.import_custom(make_wav(self.data.root / "chime.wav", seconds=0.3))

        self.audio.start_alert(sound.id)

        self.assertTrue(pump_until(lambda: self.audio.alert_loop_count >= 2))

    def test_preview_plays_once_and_reports_when_it_ends(self):
        sound = self.library.import_custom(make_wav(self.data.root / "chime.wav", seconds=0.3))
        ended = []

        self.assertTrue(self.audio.preview(sound.id, on_finished=lambda: ended.append(True)))

        self.assertTrue(pump_until(lambda: ended))
        self.assertEqual(self.audio.preview_state(), "null")
        self.assertIsNone(self.audio.preview_sound_id)

    def test_preview_is_refused_while_an_alert_is_sounding(self):
        self.audio.start_alert("bundled-community")

        self.assertFalse(self.audio.preview("bundled-funny"))

    def test_alert_stops_a_running_preview(self):
        self.audio.preview("bundled-funny")
        pump_until(lambda: self.audio.preview_state() == "playing")

        self.audio.start_alert("bundled-community")

        self.assertEqual(self.audio.preview_state(), "null")

    def test_missing_sound_falls_back_to_the_default_alert(self):
        self.data.repo.add_sound(Sound("custom-gone", "Gone", "custom", "gone.wav", 1.0))

        self.audio.start_alert("custom-gone")

        self.assertTrue(pump_until(lambda: self.audio.alert_state() == "playing"))
        self.assertEqual(self.audio.alert_sound_id, "custom-gone")
        self.assertIsNotNone(self.audio.last_error)

    def test_volume_and_mute_are_applied(self):
        self.audio.set_volume(40)
        self.audio.start_alert("bundled-community")
        pump_until(lambda: self.audio.alert_state() == "playing")
        self.assertAlmostEqual(self.audio.effective_volume, 0.4)

        self.audio.set_muted(True)

        self.assertEqual(self.audio.effective_volume, 0.0)


if __name__ == "__main__":
    unittest.main()
