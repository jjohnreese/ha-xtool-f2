"""Run without Home Assistant: python -m unittest discover -s tests -p test_f2_repeat.py."""

import asyncio
import importlib.util
from pathlib import Path
import unittest

_path = Path(__file__).resolve().parents[1] / "custom_components/xtool/f2_repeat.py"
_spec = importlib.util.spec_from_file_location("f2_repeat_under_test", _path)
repeat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repeat)


def state(mode="P_IDLE", submode="", alarm=None):
    return {"curMode": {"mode": mode, "subMode": submode}, "curAlarmInfo": {"alarm": alarm}}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.identity = {"deviceCode": "GS006", "firmware": {"package_version": "40.56.024.00.ht3"}}
        self.runtime = state()
        self.prepared = False
        self.digest = "0123456789abcdef" * 2
        self.last_digest = self.digest
        self.changed_digest = None
        self.prepare_error = False
        self.start_error = False
        self.ready_mode = state("Work", "workReady")
        self.closed = False
        self.wait = None
        self.active_channels = set()
        self.cleanup_error = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def request(self, path, method="GET", *, params=None, data=None):
        self.calls.append((path, method, params, data))
        if self.wait is not None:
            await self.wait.wait()
        if path.endswith("machineInfo"):
            return self.identity
        if path == repeat.RUNTIME:
            return self.ready_mode if self.prepared else self.runtime
        if path == repeat.FILE_METADATA:
            channel = data["channel"]
            if channel in self.active_channels:
                raise repeat.RepeatJobError("channel is busy")
            self.active_channels.add(channel)
            filename = data["filename"]
            digest = self.last_digest if filename == "last.xf" else self.digest
            if self.prepared and self.changed_digest is not None:
                digest = self.changed_digest
            return {"filename": filename, "filetype": 1, "filesize": 4096, "digesttype": 1, "digestdata": digest}
        if path == repeat.FILE_FINISH:
            if self.cleanup_error:
                raise repeat.RepeatJobError("metadata cleanup failed")
            assert data["code"] == -1
            self.active_channels.remove(data["channel"])
            return {}
        if path == repeat.PREPARE:
            if self.prepare_error:
                raise RuntimeError("rejected")
            self.prepared = True
            return {}
        if path == repeat.START:
            if self.start_error:
                raise TimeoutError()
            return {}
        raise AssertionError(path)

    def calls_to(self, path):
        return [call for call in self.calls if call[0] == path]


class F2RepeatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeClient()
        self.control = repeat.F2RepeatController(lambda: self.client)

    async def rejected_without_start(self):
        with self.assertRaises((repeat.RepeatJobError, RuntimeError)):
            await self.control.run()
        self.assertFalse(self.client.calls_to(repeat.START))
        self.assertTrue(self.client.closed)

    async def test_prepare_once_and_start_exactly_once(self):
        await self.control.run()
        self.assertEqual(self.client.calls_to(repeat.PREPARE), [
            (repeat.PREPARE, "PUT", None, {"fileType": "xf", "autoStart": 0, "loopPrint": 0}),
        ])
        self.assertEqual(self.client.calls_to(repeat.START), [
            (repeat.START, "PUT", {"action": "start"}, {}),
        ])
        self.assertTrue(self.client.closed)

    async def test_prepare_only_never_starts(self):
        await self.control.run(prepare_only=True)
        self.assertEqual(len(self.client.calls_to(repeat.PREPARE)), 1)
        self.assertFalse(self.client.calls_to(repeat.START))

    async def test_ht4_candidate_preserves_single_start_and_releases_channels(self):
        self.client.identity["firmware"]["package_version"] = "40.56.025.00.ht4"
        await self.control.run()
        self.assertEqual(len(self.client.calls_to(repeat.START)), 1)
        self.assertFalse(self.client.active_channels)

    async def test_ht4_candidate_still_refuses_working_job(self):
        self.client.identity["firmware"]["package_version"] = "40.56.025.00.ht4"
        self.client.runtime = state("Work", "working")
        await self.rejected_without_start()
        self.assertFalse(self.client.calls_to(repeat.PREPARE))

    async def test_busy_paused_ready_sleep_and_unknown_are_refused(self):
        for mode in ["P_WORKING", "P_PAUSE", "P_READY", "P_SLEEP", "Work", "unknown"]:
            with self.subTest(mode=mode):
                self.client.runtime = state(mode)
                await self.rejected_without_start()
                self.assertFalse(self.client.calls_to(repeat.PREPARE))

    async def test_current_alarm_is_refused(self):
        self.client.runtime = state(alarm=[{"code": 1}])
        await self.rejected_without_start()

    async def test_missing_alarm_status_is_refused(self):
        self.client.runtime.pop("curAlarmInfo")
        await self.rejected_without_start()

    async def test_other_model_and_unverified_firmware_are_refused(self):
        for identity in [{"deviceCode": "GS004"}, {"deviceCode": "GS006", "firmware": {"package_version": "future"}}]:
            with self.subTest(identity=identity):
                self.client.identity = identity
                await self.rejected_without_start()
                self.assertFalse(self.client.calls_to(repeat.PREPARE))

    async def test_different_previous_job_is_refused(self):
        self.client.last_digest = "fedcba9876543210" * 2
        await self.rejected_without_start()
        self.assertFalse(self.client.calls_to(repeat.PREPARE))

    async def test_invalid_or_missing_cached_job_is_refused(self):
        self.client.digest = ""
        await self.rejected_without_start()

    async def test_prepare_rejection_never_sends_start(self):
        self.client.prepare_error = True
        await self.rejected_without_start()

    async def test_alarm_after_prepare_never_sends_start(self):
        self.client.ready_mode = state("Work", "workReady", [1])
        await self.rejected_without_start()

    async def test_bad_prepared_state_never_sends_start(self):
        self.client.ready_mode = state("P_WORKING")
        await self.rejected_without_start()

    async def test_changed_upload_after_preparation_is_refused(self):
        self.client.changed_digest = "fedcba9876543210" * 2
        await self.rejected_without_start()

    async def test_start_timeout_is_never_retried(self):
        self.client.start_error = True
        with self.assertRaisesRegex(repeat.RepeatJobError, "uncertain"):
            await self.control.run()
        self.assertEqual(len(self.client.calls_to(repeat.START)), 1)

    async def test_immediate_second_press_is_refused(self):
        await self.control.run()
        with self.assertRaisesRegex(repeat.RepeatJobError, "already requested"):
            await self.control.run()
        self.assertEqual(len(self.client.calls_to(repeat.START)), 1)

    async def test_concurrent_press_is_not_queued(self):
        self.client.wait = asyncio.Event()
        first = asyncio.create_task(self.control.run())
        await asyncio.sleep(0)
        with self.assertRaisesRegex(repeat.RepeatJobError, "already requested"):
            await self.control.run()
        self.client.wait.set()
        await first
        self.assertEqual(len(self.client.calls_to(repeat.START)), 1)

    async def test_second_finished_job_reuses_cleaned_channels(self):
        await self.control.run()
        self.assertFalse(self.client.active_channels)
        self.client.prepared = False  # The first job completed and became idle.
        self.control._last_start -= 6
        await self.control.run()
        self.assertFalse(self.client.active_channels)
        self.assertEqual(len(self.client.calls_to(repeat.START)), 2)
        self.assertEqual(len(self.client.calls_to(repeat.FILE_FINISH)), 6)

    async def test_invalid_metadata_still_releases_our_channel(self):
        self.client.digest = ""
        await self.rejected_without_start()
        self.assertFalse(self.client.active_channels)

    async def test_cleanup_failure_prevents_preparation(self):
        self.client.cleanup_error = True
        await self.rejected_without_start()
        self.assertFalse(self.client.calls_to(repeat.PREPARE))

    async def test_busy_channel_is_never_cancelled(self):
        self.client.active_channels.add(210)
        await self.rejected_without_start()
        self.assertFalse(self.client.calls_to(repeat.FILE_FINISH))
        self.assertEqual(self.client.active_channels, {210})


if __name__ == "__main__":
    unittest.main()
