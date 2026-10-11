"""Requests that arrive as screenshots: payload parsing, scanning and the watcher path."""
from __future__ import annotations

import json
import os
import time
from functools import partial

from mforge import agent, stripcodec, watcher
from mforge.errors import ValidationError
from mforge.shots import ShotScanner, request_from_payload
from mforge.validate import parse_request
from tests.test_agent_watcher import NEW_MACRO, WatcherCase, add_macro, fake_process
from tests.test_strip import make_tga

FIELDS = "\t".join(["MFREQ", "4242", "30", "WARLOCK", "Test Char", "add a corruption macro"])


class PayloadTests(WatcherCase):
    def test_valid_payload(self):
        self.assertEqual(request_from_payload(FIELDS.encode()),
                         {"id": 4242, "level": 30, "class": "WARLOCK", "char": "Test Char",
                          "text": "add a corruption macro", "mode": "macro", "scope": "char"})

    def test_a_seventh_field_sets_the_mode(self):
        self.assertEqual(request_from_payload((FIELDS + "	script").encode())["mode"], "script")

    def test_an_unknown_mode_is_refused_when_the_request_is_validated(self):
        raw = request_from_payload((FIELDS + "	bogus").encode())
        with self.assertRaises(ValidationError):
            parse_request(raw, {"warlock"})

    def test_bad_payloads(self):
        for bad in (b"hello", b"MFREQ\t1\t2", b"MFREQ\tx\t30\tWARLOCK\tA\tB", b"\xff\xfe",
                    b"OTHER\t1\t30\tWARLOCK\tA\tB", FIELDS.encode() + b"\textra\textra\textra"):
            with self.assertRaises(ValidationError, msg=bad):
                request_from_payload(bad)


class ScreenshotWatcherTests(WatcherCase):
    def setUp(self):
        super().setUp()
        self.folder = self.sb.wow / "Screenshots"
        self.folder.mkdir(exist_ok=True)
        self.scanner = ShotScanner(self.folder, started=time.time() - 30)
        self.runner = partial(agent.run_agent, runner=fake_process(add_macro(NEW_MACRO)))

    def shot(self, name="WoWScrnShot_1.tga", payload=FIELDS, age=5.0, data=None):
        path = self.folder / name
        cells = stripcodec.encode_cells(1, payload.encode())
        path.write_bytes(data if data is not None else make_tga(cells))
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def run_once(self):
        return watcher.process_new_shots(self.settings, self.state, self.scanner, self.runner)

    def replies(self):
        return json.loads((self.sb.project / "data" / "replies.json").read_text())

    def test_request_in_a_picture_is_answered(self):
        self.shot()
        self.assertEqual(self.run_once(), 1)
        self.assertEqual(self.state.handled, {4242})
        self.assertEqual(self.replies()[0]["req"], 4242)
        self.assertIn("Corr", self.spec_names())

    def test_same_picture_is_not_handled_twice(self):
        self.shot()
        self.assertEqual(self.run_once(), 1)
        self.assertEqual(self.run_once(), 0)

    def test_repeated_request_id_is_ignored(self):
        self.shot("a.tga")
        self.shot("b.tga")
        self.assertEqual(self.run_once(), 1)

    def test_pictures_are_kept_when_deletion_is_turned_off(self):
        import dataclasses
        self.settings = dataclasses.replace(self.settings, delete_request_shots=False)
        path = self.shot()
        self.run_once()
        self.assertTrue(path.exists())

    def test_deletion_is_on_by_default_and_removes_the_request_picture_only(self):
        self.assertTrue(self.settings.delete_request_shots)
        path = self.shot()
        stamp = path.stat().st_mtime
        jpg = self.folder / "WoWScrnShot_1.jpg"
        jpg.write_bytes(b"my own screenshot")
        os.utime(jpg, (stamp + 2, stamp + 2))
        self.run_once()
        self.assertFalse(path.exists())
        self.assertTrue(jpg.exists())

    def test_pictures_from_before_the_watcher_started_are_ignored(self):
        self.shot(age=3600)
        self.assertEqual(self.run_once(), 0)

    def test_pictures_that_are_not_requests_do_not_stop_the_watcher(self):
        self.shot("plain.tga", data=make_tga([]))
        self.shot("junk.tga", data=b"\0" * 50)
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(self.state.handled, set())

    def test_hostile_class_in_a_picture_is_rejected_with_a_reply(self):
        self.shot(payload="\t".join(["MFREQ", "77", "30", "../../evil", "Test Char", "x"]))
        self.assertEqual(self.run_once(), 1)
        self.assertIn("Request rejected", self.replies()[0]["text"])
        last = (self.sb.project / "data" / "watch.log").read_text().splitlines()[-1]
        self.assertIn("rejected", json.loads(last))

    def test_a_picture_still_being_written_is_retried_later(self):
        self.shot(age=0.0)
        self.assertEqual(self.run_once(), 0)  # too fresh: not read yet
        self.assertEqual(self.scanner.seen, {})


class AcknowledgementTests(WatcherCase):
    def replies(self):
        return json.loads((self.sb.project / "data" / "replies.json").read_text())

    def test_pending_replies_are_marked_in_the_game_data(self):
        from mforge.lua import lua_replies
        text = lua_replies([{"id": 1, "req": 5, "text": "hi", "pending": True},
                            {"id": 2, "req": 5, "text": "done"}])
        self.assertIn('text = "hi", pending = true }', text)
        self.assertNotIn("pending = true }", text.splitlines()[1])

    def test_ack_is_sent_first_and_replaced_by_the_final_reply(self):
        seen = []

        def edit(workspace):
            seen.append(self.replies())  # what the game could see while the agent works
            add_macro(NEW_MACRO)(workspace)

        self.handle(runner=fake_process(edit))
        self.assertTrue(seen[0][0]["pending"])
        self.assertEqual(seen[0][0]["req"], 100)
        final = self.replies()
        self.assertEqual(len(final), 1)
        self.assertNotIn("pending", final[0])
        data = (self.sb.project / "build" / "MacroForge" / "MacroForgeData.lua").read_text()
        self.assertNotIn("pending", data)

    def test_no_ack_when_rate_limited(self):
        self.state.recent = [time.time()] * self.settings.max_requests_per_hour
        self.handle()
        self.assertEqual([r.get("pending") for r in self.replies()], [None])


class DetachedStartTests(WatcherCase):
    def test_launch_breaks_away_from_the_job_and_falls_back(self):
        from unittest import mock
        from mforge import startup
        calls = []

        def popen(command, creationflags=0, close_fds=False):
            calls.append(creationflags)
            if creationflags & startup.BREAKAWAY:
                raise OSError("job does not allow breakaway")

        with mock.patch.object(startup.subprocess, "Popen", popen):
            startup.launch_detached(["x"])
        self.assertEqual(calls, [startup.DETACHED | startup.BREAKAWAY, startup.DETACHED])


class LeftoverPictureTests(ScreenshotWatcherTests):
    """Nothing the request flow creates may be left behind, and nothing else is ever touched.

    Watching a real request (a monitor on the Screenshots folder, with and without a /reload first)
    showed exactly one file per request: the TGA. The addon calls ``Screenshot()`` once.
    """

    def keep(self, name, stamp, data=b"mine"):
        path = self.folder / name
        path.write_bytes(data)
        os.utime(path, (stamp, stamp))
        return path

    def test_a_second_picture_of_an_answered_request_is_deleted_too(self):
        first = self.shot("a.tga")
        second = self.shot("b.tga")
        self.assertEqual(self.run_once(), 1)
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())

    def test_the_players_own_screenshots_are_never_touched_whatever_their_time_or_type(self):
        path = self.shot()
        stamp = path.stat().st_mtime
        mine = [self.keep(f"shot{i}{ext}", stamp + offset)
                for i, (offset, ext) in enumerate([(-300, ".jpg"), (-5, ".jpg"), (-1, ".jpg"),
                                                   (0.5, ".jpg"), (1.5, ".jpg"), (4, ".jpg"),
                                                   (9, ".jpg"), (20, ".jpg"), (1, ".jpeg"),
                                                   (1, ".png"), (1, ".bmp")])]
        self.run_once()
        self.run_once()
        self.assertFalse(path.exists())
        for picture in mine:
            self.assertTrue(picture.exists(), f"{picture.name} is the player's own screenshot")

    def test_a_player_screenshot_in_tga_format_that_is_not_a_request_is_kept(self):
        plain = self.shot("plain.tga", data=make_tga([]))
        self.run_once()
        self.assertTrue(plain.exists())

    def test_an_old_request_picture_nobody_answered_is_removed_but_not_answered(self):
        old = self.shot("old.tga", age=3600)
        jpg = self.keep("old.jpg", old.stat().st_mtime + 1.5)
        self.assertEqual(self.run_once(), 0)
        self.assertEqual(self.state.handled, set())
        self.assertFalse(old.exists())
        self.assertTrue(jpg.exists())
        self.assertNotIn("Corr", self.spec_names())  # it was not run

    def test_an_old_picture_that_is_not_a_request_is_left_alone(self):
        plain = self.shot("plain.tga", data=make_tga([]), age=3600)
        junk = self.shot("junk.tga", data=bytes(50), age=3600)
        self.run_once()
        self.assertTrue(plain.exists() and junk.exists())

    def test_very_old_pictures_are_not_even_looked_at(self):
        from mforge.shots import STALE_MAX_AGE
        ancient = self.shot("ancient.tga", age=STALE_MAX_AGE + 3600)
        self.run_once()
        self.assertTrue(ancient.exists())

    def test_old_pictures_are_left_when_deletion_is_turned_off(self):
        import dataclasses
        self.settings = dataclasses.replace(self.settings, delete_request_shots=False)
        old = self.shot("old.tga", age=3600)
        self.run_once()
        self.assertTrue(old.exists())

    def test_only_a_few_old_pictures_are_examined_per_poll(self):
        from mforge.shots import STALE_LOOK_LIMIT
        olds = [self.shot(f"old{i}.tga", age=3600 + i) for i in range(STALE_LOOK_LIMIT + 3)]
        self.run_once()
        self.assertEqual(sum(not p.exists() for p in olds), STALE_LOOK_LIMIT)
        self.run_once()
        self.assertEqual(sum(not p.exists() for p in olds), STALE_LOOK_LIMIT + 3)


class StuckPictureTests(ScreenshotWatcherTests):
    """A picture the game still holds open must not break or stall the watcher."""

    def locked(self, names):
        from pathlib import Path
        from unittest import mock
        real = Path.unlink

        def unlink(path, missing_ok=False):
            if path.name in names:
                raise PermissionError("in use by another process")
            return real(path, missing_ok=missing_ok)

        return mock.patch.object(Path, "unlink", unlink)

    def test_locked_picture_is_retried_and_the_request_still_answered(self):
        path = self.shot()
        with self.locked({path.name}):
            self.assertEqual(self.run_once(), 1)
            self.assertIn(path, self.scanner.stuck)
            self.assertEqual(self.replies()[0]["req"], 4242)
            self.run_once()  # a retry that fails again
            self.assertEqual(self.scanner.stuck[path], 1)
        self.run_once()  # the lock is gone
        self.assertFalse(path.exists())
        self.assertEqual(self.scanner.stuck, {})

    def test_gives_up_after_a_while_with_a_warning(self):
        from mforge.shots import STUCK_RETRIES
        path = self.shot()
        with self.locked({path.name}), self.assertLogs("mforge.shots", "WARNING") as logs:
            self.run_once()
            for _ in range(STUCK_RETRIES):
                self.run_once()
        self.assertEqual(self.scanner.stuck, {})
        self.assertTrue(path.exists())
        self.assertIn("delete it by hand", "".join(logs.output))

    def test_cleanup_reports_a_locked_file_and_ignores_a_missing_one(self):
        from mforge.shots import cleanup
        path = self.shot()
        with self.locked({path.name}):
            self.assertEqual(cleanup(path), [path])
        self.assertEqual(cleanup(path), [])  # deleted now
        self.assertEqual(cleanup(path), [])  # already gone
