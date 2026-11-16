import os
import shutil
import stat
import unittest
from unittest import mock

import _bootstrap  # noqa: F401

from productime.domain.models import Sound
from productime.services import audio_service as audio
from productime.services.audio_service import AudioImportError, SoundLibrary, SoundResolveError
from support import ASSETS, TempData, alarm_draft, make_wav


class AudioSecurityTestCase(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.addCleanup(self.data.close)
        self.repo = self.data.repo
        self.sounds_dir = self.data.data_dir / "sounds"
        self.library = SoundLibrary(self.repo, ASSETS, self.sounds_dir)
        self.inbox = self.data.root / "inbox"
        self.inbox.mkdir()

    def stored_files(self):
        return sorted(p.name for p in self.sounds_dir.iterdir()) if self.sounds_dir.exists() else []

    def custom_rows(self):
        return [s for s in self.repo.list_sounds() if s.source_type == "custom"]


class MagicByteTests(unittest.TestCase):
    def test_known_audio_headers_are_recognised(self):
        self.assertEqual(audio.sniff_audio_kind(b"RIFF\x24\x00\x00\x00WAVEfmt "), "wav")
        self.assertEqual(audio.sniff_audio_kind(b"OggS\x00\x02" + b"\x00" * 10), "ogg")
        self.assertEqual(audio.sniff_audio_kind(b"fLaC\x00\x00\x00\x22" + b"\x00" * 8), "flac")
        self.assertEqual(audio.sniff_audio_kind(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 4), "m4a")
        self.assertEqual(audio.sniff_audio_kind(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 6), "mp3")
        self.assertEqual(audio.sniff_audio_kind(b"\xff\xfb\x90\x00" + b"\x00" * 12), "mp3")

    def test_non_audio_headers_are_rejected(self):
        for header in (
            b"",
            b"#!/bin/sh\necho hi\n",
            b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8,
            b"<html><body>",
            b"RIFF\x24\x00\x00\x00AVI LIST",
            b"PK\x03\x04" + b"\x00" * 12,
        ):
            with self.subTest(header=header[:8]):
                self.assertIsNone(audio.sniff_audio_kind(header))

    def test_every_bundled_asset_sniffs_as_mp3(self):
        for path in sorted(ASSETS.glob("*.mp3")):
            with open(path, "rb") as handle:
                self.assertEqual(audio.sniff_audio_kind(handle.read(16)), "mp3", path.name)


class CustomImportTests(AudioSecurityTestCase):
    def test_valid_wav_is_copied_under_a_generated_private_name(self):
        source = make_wav(self.inbox / "My Chime.wav")

        sound = self.library.import_custom(source)

        self.assertEqual((sound.display_name, sound.source_type), ("My Chime", "custom"))
        self.assertRegex(sound.id, r"^custom-[0-9a-f]{32}$")
        self.assertRegex(sound.file_path, r"^[0-9a-f]{32}\.wav$")
        stored = self.sounds_dir / sound.file_path
        self.assertEqual(stored.read_bytes(), source.read_bytes())
        self.assertEqual(stat.S_IMODE(stored.stat().st_mode), 0o600)
        self.assertGreater(sound.duration_seconds, 0.1)
        self.assertEqual(self.repo.get_sound(sound.id), sound)
        self.assertTrue(source.exists())

    def test_real_mp3_is_accepted(self):
        source = self.inbox / "song.mp3"
        shutil.copyfile(ASSETS / "community-alarm.mp3", source)

        sound = self.library.import_custom(source)

        self.assertAlmostEqual(sound.duration_seconds, 6.1, delta=0.5)
        self.assertEqual(self.library.resolve(sound.id), (self.sounds_dir / sound.file_path).resolve())

    def test_disallowed_extensions_are_rejected(self):
        wav = make_wav(self.inbox / "ok.wav")
        for name in ("song.txt", "song.exe", "song.mp3.sh", "song", "song.aiff"):
            target = self.inbox / name
            shutil.copyfile(wav, target)
            with self.subTest(name=name), self.assertRaises(AudioImportError):
                self.library.import_custom(target)

        self.assertEqual((self.stored_files(), self.custom_rows()), ([], []))

    def test_extension_that_disagrees_with_the_content_is_rejected(self):
        disguised = self.inbox / "really-a-wav.mp3"
        shutil.copyfile(make_wav(self.inbox / "ok.wav"), disguised)

        with self.assertRaises(AudioImportError):
            self.library.import_custom(disguised)

    def test_script_renamed_to_mp3_is_rejected(self):
        fake = self.inbox / "payload.mp3"
        fake.write_text("#!/bin/sh\nrm -rf ~\n", encoding="utf-8")

        with self.assertRaises(AudioImportError):
            self.library.import_custom(fake)

        self.assertEqual((self.stored_files(), self.custom_rows()), ([], []))

    def test_valid_header_with_garbage_body_is_rejected_and_leaves_no_file(self):
        fake = self.inbox / "broken.mp3"
        fake.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"not audio at all " * 200)

        with self.assertRaises(AudioImportError):
            self.library.import_custom(fake)

        self.assertEqual((self.stored_files(), self.custom_rows()), ([], []))

    def test_empty_missing_and_directory_sources_are_rejected(self):
        empty = self.inbox / "empty.mp3"
        empty.touch()
        folder = self.inbox / "folder.mp3"
        folder.mkdir()

        for source in (empty, folder, self.inbox / "missing.mp3"):
            with self.subTest(source=source.name), self.assertRaises(AudioImportError):
                self.library.import_custom(source)

    def test_file_over_the_size_cap_is_rejected(self):
        source = make_wav(self.inbox / "long.wav", seconds=1.0)

        with mock.patch.object(audio, "MAX_IMPORT_BYTES", 1024), self.assertRaises(AudioImportError):
            self.library.import_custom(source)

        self.assertEqual(self.stored_files(), [])

    def test_size_cap_is_fifty_megabytes(self):
        self.assertEqual(audio.MAX_IMPORT_BYTES, 50 * 1024 * 1024)

    def test_symlink_to_a_non_audio_file_is_rejected(self):
        secret = self.data.root / "secret.txt"
        secret.write_text("private notes", encoding="utf-8")
        link = self.inbox / "innocent.mp3"
        os.symlink(secret, link)

        with self.assertRaises(AudioImportError):
            self.library.import_custom(link)

    def test_hostile_file_name_becomes_a_safe_display_name(self):
        source = make_wav(self.inbox / "<b>loud<b>\x07 & proud.wav")

        sound = self.library.import_custom(source)

        self.assertEqual(sound.display_name, "<b>loud<b> & proud")
        self.assertNotIn("<", sound.file_path)

    def test_removing_a_custom_sound_deletes_the_file_and_repoints_alarms(self):
        sound = self.library.import_custom(make_wav(self.inbox / "chime.wav"))
        alarm = self.repo.create_alarm(alarm_draft(sound_id=sound.id))

        self.library.remove_custom(sound.id)

        self.assertEqual(self.stored_files(), [])
        self.assertEqual(self.repo.get_alarm(alarm.id).sound_id, "bundled-digital")


class SoundResolutionTests(AudioSecurityTestCase):
    def test_bundled_sound_resolves_inside_assets(self):
        path = self.library.resolve("bundled-digital")

        self.assertEqual(path, (ASSETS / "digital-alarm-clock.mp3").resolve())

    def test_unknown_and_malformed_ids_are_rejected(self):
        for sound_id in ("bundled-missing", "../../etc/passwd", "", "custom-x/../../y", "a" * 200, None):
            with self.subTest(sound_id=sound_id), self.assertRaises(SoundResolveError):
                self.library.resolve(sound_id)

    def test_database_row_pointing_outside_the_sounds_folder_is_rejected(self):
        outside = self.data.root / "outside.wav"
        make_wav(outside)
        self.sounds_dir.mkdir(parents=True, exist_ok=True)
        for index, file_path in enumerate(("../../outside.wav", str(outside), "sub/../../outside.wav")):
            sound_id = f"custom-evil{index}"
            self.repo.add_sound(Sound(sound_id, "Evil", "custom", file_path, 1.0))
            with self.subTest(file_path=file_path), self.assertRaises(SoundResolveError):
                self.library.resolve(sound_id)

    def test_symlink_inside_the_sounds_folder_that_escapes_is_rejected(self):
        outside = make_wav(self.data.root / "outside.wav")
        self.sounds_dir.mkdir(parents=True, exist_ok=True)
        os.symlink(outside, self.sounds_dir / "linked.wav")
        self.repo.add_sound(Sound("custom-link", "Link", "custom", "linked.wav", 1.0))

        with self.assertRaises(SoundResolveError):
            self.library.resolve("custom-link")

    def test_row_whose_file_is_gone_is_rejected(self):
        self.repo.add_sound(Sound("custom-gone", "Gone", "custom", "gone.wav", 1.0))

        with self.assertRaises(SoundResolveError):
            self.library.resolve("custom-gone")


if __name__ == "__main__":
    unittest.main()
