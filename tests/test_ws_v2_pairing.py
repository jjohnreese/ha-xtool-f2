"""Offline WS-V2 auth regressions; no Home Assistant or device required.

python -m unittest discover -s tests -p test_ws_v2_pairing.py -v
"""

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp


ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "xtool"
PREFIX = "xtool_offline_pairing_tests"
for name, path in (
    (PREFIX, ROOT),
    (f"{PREFIX}.protocols", ROOT / "protocols"),
    (f"{PREFIX}.protocols.ws_v2", ROOT / "protocols/ws_v2"),
):
    package = ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package

# Only the SSL helper is imported eagerly. Actual production protocol and base
# classes are loaded unchanged, without the HA setup/coordinator import cycle.
SSL_MODULES = {name: ModuleType(name) for name in (
    "homeassistant", "homeassistant.util", "homeassistant.util.ssl",
)}
SSL_MODULES["homeassistant.util.ssl"].get_default_no_verify_context = lambda: None


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    with patch.dict(sys.modules, SSL_MODULES):
        spec.loader.exec_module(module)
    return module


protocol = load(f"{PREFIX}.protocols.ws_v2.protocol", ROOT / "protocols/ws_v2/protocol.py")
repeat = load(f"{PREFIX}.f2_repeat", ROOT / "f2_repeat.py")


def decode(raw):
    frames, remainder = protocol._decode_frames(raw)
    assert not remainder and len(frames) == 1
    return json.loads(frames[0][1])


class PairingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Fail closed if a regression accidentally tries to open real sockets.
        self.network = patch("aiohttp.ClientSession", side_effect=AssertionError("Network forbidden in offline tests"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.client = protocol.WSV2Protocol("offline.invalid")

    async def test_saved_pairing_token_returns_new_session_and_rotation_callback(self):
        self.client.pairing_token = "saved-test-token"
        self.client.on_pairing_token = Mock()
        self.client.request = AsyncMock(return_value={"token": "rotated-test-token", "sessionToken": "new-test-session"})
        await self.client._send_first_message(True)
        self.assertEqual(self.client.request.call_args.kwargs["data"]["token"], "saved-test-token")
        self.assertEqual(self.client.pairing_token, "rotated-test-token")
        self.assertEqual(self.client._session_token, "new-test-session")
        self.client.on_pairing_token.assert_called_once_with("rotated-test-token")

    async def test_legacy_parity_still_accepts_empty_result(self):
        self.client.request = AsyncMock(return_value={})
        await self.client._send_first_message(False)
        self.assertNotIn("token", self.client.request.call_args.kwargs["data"])
        self.assertEqual(self.client._session_token, "")

    async def test_background_connect_never_waits_for_button_or_retries_406(self):
        err = protocol.WSV2RequestError("GET", "/v1/user/parity", 406, "need button confirm", {})
        self.client._open_and_handshake = AsyncMock(side_effect=protocol._ParityFailed(err))
        with self.assertRaises(protocol.WSV2PairingRequired):
            await self.client._open_instruction_ws()
        self.client._open_and_handshake.assert_awaited_once()

    async def test_explicit_pairing_polls_auth_status_and_receives_token(self):
        err = protocol.WSV2RequestError("GET", "/v1/user/parity", 406, "need button confirm", {})
        self.client.request = AsyncMock(side_effect=[err, {"token": "paired-test-token", "sessionToken": "paired-test-session"}])
        self.client._ws_instr = SimpleNamespace(closed=False)
        self.client._pairing_deadline = protocol.time.monotonic() + 30
        with patch.object(protocol.asyncio, "sleep", new=AsyncMock()):
            await self.client._send_first_message(True)
        self.assertEqual(self.client.request.await_args_list[1].args[0], "/v1/user/auth-status")
        self.assertEqual(self.client._session_token, "paired-test-session")

    async def test_pairing_timeout_and_blocked_responses_are_explicit(self):
        for code, reason in ((410, "timeout"), (409, "blocked"), (503, "blocked")):
            with self.subTest(code=code):
                self.client.request = AsyncMock(side_effect=protocol.WSV2RequestError("GET", "/v1/user/auth-status", code, "test", {}))
                self.client._ws_instr = SimpleNamespace(closed=False)
                self.client._pairing_deadline = protocol.time.monotonic() + 30
                with patch.object(protocol.asyncio, "sleep", new=AsyncMock()):
                    with self.assertRaises(protocol.WSV2PairingFailed) as raised:
                        await self.client._await_button_confirm()
                self.assertEqual(raised.exception.reason, reason)

    async def test_legacy_fallback_only_repeats_parity_not_commands(self):
        self.client._open_and_handshake = AsyncMock(side_effect=[protocol._ParityFailed(RuntimeError("old firmware")), None])
        await self.client._open_instruction_ws()
        self.assertEqual([call.args for call in self.client._open_and_handshake.await_args_list], [(True,), (False,)])

    async def test_saved_token_failure_does_not_fall_back_to_guest(self):
        self.client.pairing_token = "saved-test-token"
        self.client._open_and_handshake = AsyncMock(side_effect=protocol._ParityFailed(RuntimeError("rejected")))
        with self.assertRaises(ConnectionError):
            await self.client._open_instruction_ws()
        self.client._open_and_handshake.assert_awaited_once()

    async def test_normal_requests_send_session_token_and_redact_pairing_secrets(self):
        self.client._session_token = "secret-test-session"
        sent = []

        async def send(raw):
            payload = decode(raw)
            sent.append(payload)
            self.client._dispatch_event({"type": "response", "transactionId": payload["transactionId"], "code": 0, "data": {"token": "secret-test-response"}})

        self.client._ws_instr = SimpleNamespace(send_bytes=send)
        with self.assertLogs(protocol._LOGGER, level="DEBUG") as logs:
            await self.client.request("/v1/device/runtime-infos", data={"token": "secret-test-pairing"}, _skip_connect=True)
        self.assertEqual(sent[0]["sessionToken"], "secret-test-session")
        for secret in ("secret-test-session", "secret-test-pairing", "secret-test-response"):
            self.assertNotIn(secret, "\n".join(logs.output))

    async def test_heartbeat_carries_session_token(self):
        self.client._connected = True
        self.client._session_token = "heartbeat-test-session"
        sent = []

        async def send(raw):
            payload = decode(raw)
            sent.append(payload)
            self.client._dispatch_event({"type": "response", "transactionId": payload["transactionId"], "code": 0})
            self.client._connected = False

        self.client._ws_instr = SimpleNamespace(closed=False, send_bytes=send)
        with patch.object(protocol.asyncio, "sleep", new=AsyncMock()):
            await self.client._heartbeat_loop()
        self.assertEqual(sent[0]["url"], "/v1/user/ping")
        self.assertEqual(sent[0]["sessionToken"], "heartbeat-test-session")

    async def test_disconnect_clears_session_but_retains_pairing_token(self):
        self.client._session_token = "obsolete-test-session"
        self.client.pairing_token = "saved-test-token"
        await self.client.disconnect()
        self.assertEqual(self.client._session_token, "")
        self.assertEqual(self.client.pairing_token, "saved-test-token")

    async def test_rejected_heartbeat_closes_connection(self):
        self.client._connected = True
        self.client._close_quiet = AsyncMock()

        async def send(raw):
            self.client._dispatch_event({"type": "response", "transactionId": decode(raw)["transactionId"], "code": 403})

        self.client._ws_instr = SimpleNamespace(closed=False, send_bytes=send)
        with patch.object(protocol.asyncio, "sleep", new=AsyncMock()):
            await self.client._heartbeat_loop()
        # The close is scheduled so it does not cancel/await the heartbeat itself.
        await asyncio.sleep(0)
        self.client._close_quiet.assert_awaited_once()

    async def test_binary_and_text_fallback_logs_do_not_expose_secrets(self):
        raw = json.dumps({"type": "response", "transactionId": 42, "data": {"token": "secret-test-binary"}})

        async def messages():
            yield SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=raw)

        self.client._ws_instr = messages()
        with self.assertLogs(protocol._LOGGER, level="DEBUG") as logs:
            self.client._handle_binary(protocol._encode_frame(raw.encode()))
            self.client._handle_binary(protocol._encode_frame(b'{"token":"secret-test-malformed"'))
            await self.client._reader_loop()
        self.assertNotIn("secret-test-binary", "\n".join(logs.output))
        self.assertNotIn("secret-test-malformed", "\n".join(logs.output))

    async def test_repeat_button_reads_current_token_and_persists_rotation(self):
        class Entity:
            def __init__(self, coordinator):
                self.coordinator = coordinator

            def _set_unique_id(self, key):
                pass

        entity_module = ModuleType(f"{PREFIX}.entity")
        entity_module.XtoolEntity = Entity
        button_module = ModuleType("homeassistant.components.button")
        button_module.ButtonEntity = type("ButtonEntity", (), {})
        exceptions_module = ModuleType("homeassistant.exceptions")
        exceptions_module.HomeAssistantError = RuntimeError
        with patch.dict(sys.modules, {
            f"{PREFIX}.entity": entity_module,
            "homeassistant.components.button": button_module,
            "homeassistant.exceptions": exceptions_module,
        }):
            buttons = load(f"{PREFIX}.f2_repeat_button", ROOT / "f2_repeat_button.py")
        saved = Mock()
        main = SimpleNamespace(pairing_token="first-test-token", on_pairing_token=saved)
        coordinator = SimpleNamespace(host="offline.invalid", protocol=main, hass=SimpleNamespace(config=SimpleNamespace(time_zone="UTC")))
        button = buttons.F2RepeatJobButton(coordinator)
        main.pairing_token = "latest-test-token"
        client = button._repeat.client_factory()
        self.assertEqual(client.pairing_token, "latest-test-token")
        client.on_pairing_token("rotated-test-token")
        self.assertEqual(main.pairing_token, "rotated-test-token")
        saved.assert_called_once_with("rotated-test-token")

    async def test_pause_fix_survives_pairing_merge(self):
        self.assertEqual(protocol.WSV2_MODE_MAP["P_PAUSE"], protocol.XtoolStatus.PAUSED)
        self.client.request = AsyncMock(return_value={"curMode": {"mode": "Work", "subMode": "workPause"}})
        state = protocol.XtoolDeviceState()
        await self.client._poll_runtime_status(state)
        self.assertEqual(state.status, protocol.XtoolStatus.PAUSED)
        self.client._latest.clear()
        self.client._dispatch_push({
            "url": "/device/status",
            "data": {"module": "STATUS_CONTROLLER", "type": "WORK_PAUSED", "info": "working"},
        })
        self.assertEqual(self.client._latest["status"], protocol.XtoolStatus.PAUSED)

    async def test_repeat_session_uses_saved_token_and_its_own_session_token(self):
        client = repeat.F2RepeatClient("offline.invalid", "UTC", pairing_token="repeat-test-pairing", on_pairing_token=Mock())
        sent = []
        response = None

        async def send(raw):
            nonlocal response
            payload = decode(raw)
            sent.append(payload)
            data = {"token": "repeat-test-rotated", "sessionToken": "repeat-test-session"} if payload["url"] == "/v1/user/parity" else {}
            response = SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps({"transactionId": payload["transactionId"], "code": 0, "data": data}))

        async def receive():
            return response

        ws = SimpleNamespace(closed=False, send_bytes=send, receive=receive, close=AsyncMock())
        session = SimpleNamespace(ws_connect=AsyncMock(return_value=ws), close=AsyncMock())
        with patch.dict(sys.modules, SSL_MODULES), patch("aiohttp.ClientSession", return_value=session):
            async with client:
                await client.request(repeat.RUNTIME)
        self.assertEqual(sent[0]["data"]["token"], "repeat-test-pairing")
        self.assertNotIn("sessionToken", sent[0])
        self.assertEqual(sent[1]["sessionToken"], "repeat-test-session")
        client.on_pairing_token.assert_called_once_with("repeat-test-rotated")
        self.assertEqual(client._session_token, "")
        ws.close.assert_awaited_once()
        session.close.assert_awaited_once()

    async def test_repeat_pairing_refusal_never_prepares_or_starts_or_retries(self):
        client = repeat.F2RepeatClient("offline.invalid", "UTC")
        sent = []

        async def send(raw):
            sent.append(decode(raw))

        ws = SimpleNamespace(closed=False, send_bytes=send, receive=AsyncMock(return_value=SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps({"transactionId": 1, "code": 406, "data": {}}))), close=AsyncMock())
        session = SimpleNamespace(ws_connect=AsyncMock(return_value=ws), close=AsyncMock())
        with patch.dict(sys.modules, SSL_MODULES), patch("aiohttp.ClientSession", return_value=session):
            with self.assertRaisesRegex(repeat.RepeatJobError, "pair the integration"):
                async with client:
                    self.fail("Unauthenticated repeat must not enter")
        self.assertEqual([p["url"] for p in sent], ["/v1/user/parity"])
        ws.close.assert_awaited_once()
        session.close.assert_awaited_once()

    async def test_f2_exhaust_uses_device_state_for_auto_and_manual_transitions(self):
        self.client._model = SimpleNamespace(smoking_fan_uses_ext_purifier=True)
        state = protocol.XtoolDeviceState()
        for raw, expected in (("off", False), ("on", True), ("off", False), (True, True), (None, None), ("invalid", None)):
            with self.subTest(raw=raw):
                self.client._apply_peripheral_param("ext_purifier", {"state": raw, "exist": True, "current": 0.0, "power": 0.0}, state)
                self.client._apply_latest_to_state(state)
                self.assertIs(state.smoking_fan_running, expected)

    async def test_other_models_do_not_confuse_a_purifier_with_the_exhaust_fan(self):
        self.client._model = SimpleNamespace(smoking_fan_uses_ext_purifier=False)
        state = protocol.XtoolDeviceState(smoking_fan_running=False)
        self.client._apply_peripheral_param("ext_purifier", {"state": "on", "speed": 3}, state)
        self.assertIs(state.smoking_fan_running, False)
        self.assertIs(state.purifier_on, True)


if __name__ == "__main__":
    unittest.main()
