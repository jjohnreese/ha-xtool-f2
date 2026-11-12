"""Repeat the retained regular-F2 job using a short-lived Wi-Fi session.

GS006 / 40.56.024.00.ht3 had an initial physical repeat test; two consecutive
repeats were physically confirmed on 40.56.025.00.ht4. This module never
uploads a design, changes interlocks, enables auto-start, or queues another run.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

import aiohttp

SUPPORTED_FIRMWARE = frozenset({"40.56.024.00.ht3", "40.56.025.00.ht4"})
IDLE_MODES = frozenset({"P_IDLE", "P_FINISH", "P_WORK_DONE"})
RUNTIME = "/v1/device/runtime-infos"
FILE_METADATA = "/v1/filetransfer/download"
FILE_FINISH = "/v1/filetransfer/finish"
PREPARE = "/v1/processing/upload/config"
START = "/v1/processing/state"


class RepeatJobError(RuntimeError):
    """The cached job cannot safely be repeated in the current state."""


class F2RepeatClient:
    """One instruction connection; metadata channels are explicitly cancelled.

    There is deliberately no reconnect/retry after a command was sent.
    The existing integration's codec and HA's device-TLS context are reused.
    """

    def __init__(
        self, host: str, timezone: str, *, pairing_token: str = "",
        on_pairing_token: Callable[[str], None] | None = None,
    ) -> None:
        self.host = host
        self.timezone = timezone
        self.pairing_token = pairing_token
        self.on_pairing_token = on_pairing_token
        self._session_token = ""
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.transaction = 0
        self.buffer = b""

    async def __aenter__(self) -> F2RepeatClient:
        from homeassistant.util.ssl import get_default_no_verify_context

        self.session = aiohttp.ClientSession(trust_env=False)
        try:
            self.ws = await self.session.ws_connect(
                f"wss://{self.host}:28900/websocket?id={uuid.uuid4()}&function=instruction",
                ssl=get_default_no_verify_context(),
                headers={"Origin": "atomm://renderer"},
                timeout=aiohttp.ClientTimeout(total=12),
                max_msg_size=1024 * 1024,
                autoping=True,
            )
            # Public guest-protocol constant, shared with xTool Studio.
            parity_data = {
                "userID": "mk-guest", "userKey": "bWFrZWJsb2NrLXh0b29s",
                "timezone": self.timezone,
            }
            if self.pairing_token:
                parity_data["token"] = self.pairing_token
            # A repeat never initiates button-confirmation pairing or retries.
            # Pair the integration first using Home Assistant's reauth flow.
            result = await self.request("/v1/user/parity", data=parity_data)
            self._session_token = str(result.get("sessionToken") or "")
            token = str(result.get("token") or "")
            if token and token != self.pairing_token:
                self.pairing_token = token
                if self.on_pairing_token is not None:
                    self.on_pairing_token(token)
            return self
        except BaseException:
            await self.__aexit__()
            raise

    async def __aexit__(self, *_args: Any) -> None:
        self._session_token = ""
        try:
            if self.ws is not None:
                await self.ws.close()
        finally:
            if self.session is not None:
                await self.session.close()

    async def request(
        self, path: str, method: str = "GET", *,
        params: dict | None = None, data: dict | None = None,
    ) -> dict:
        from .protocols.ws_v2.protocol import _decode_frames, _encode_frame

        if self.ws is None or self.ws.closed:
            raise RepeatJobError("Repeat connection is closed; no command was retried.")
        self.transaction += 1
        payload = {
            "type": "request", "method": method, "url": path,
            "params": params or {}, "data": data or {},
            "timestamp": int(time.time() * 1000),
            "transactionId": self.transaction,
        }
        if self._session_token:
            payload["sessionToken"] = self._session_token
        async with asyncio.timeout(10):
            await self.ws.send_bytes(_encode_frame(
                json.dumps(payload, separators=(",", ":")).encode()
            ))
            while True:
                message = await self.ws.receive()
                if message.type == aiohttp.WSMsgType.BINARY:
                    raw, self.buffer = _decode_frames(self.buffer + message.data)
                    if len(self.buffer) > 1024 * 1024:
                        raise RepeatJobError("Invalid response from the F2.")
                    frames = [json.loads(blob) for kind, blob in raw if kind == 4]
                elif message.type == aiohttp.WSMsgType.TEXT:
                    frames = [json.loads(message.data)]
                else:
                    raise RepeatJobError("F2 disconnected; no command was retried.")
                for frame in frames:
                    if frame.get("transactionId") != self.transaction:
                        continue
                    if frame.get("code") != 0:
                        code = frame.get("code")
                        if code in (403, 406):
                            raise RepeatJobError(
                                "F2 authentication is required. Reconfigure/pair the integration "
                                "in Home Assistant before repeating; no command was retried."
                            )
                        message = str(frame.get("msg") or frame.get("message") or "unknown error")[:160]
                        raise RepeatJobError(f"F2 rejected {path} (code {code}: {message}); no command was retried.")
                    response = frame.get("data")
                    if not isinstance(response, dict):
                        raise RepeatJobError("Invalid response from the F2.")
                    return response


def _check_alarm(runtime: dict) -> None:
    alarm = runtime.get("curAlarmInfo")
    if not isinstance(alarm, dict) or "alarm" not in alarm:
        raise RepeatJobError("F2 alarm status is unavailable.")
    if alarm["alarm"]:
        raise RepeatJobError("Clear the F2 alarm before repeating a job.")


def _check_idle(runtime: dict) -> None:
    _check_alarm(runtime)
    if runtime.get("curMode", {}).get("mode") not in IDLE_MODES:
        raise RepeatJobError("F2 must be idle or finished before repeating a job.")


def _signature(metadata: dict, filename: str) -> tuple[int, str]:
    size, digest = metadata.get("filesize"), metadata.get("digestdata")
    if (metadata.get("filename") != filename or metadata.get("filetype") != 1
            or metadata.get("digesttype") != 1 or type(size) is not int or size <= 0
            or not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{32}", digest) is None):
        raise RepeatJobError("Cached job metadata is missing or invalid; send a job from Studio first.")
    return size, digest.lower()


class F2RepeatController:
    """Reject overlapping presses; prepare once, then issue one start."""

    def __init__(self, client_factory: Callable[[], Any]) -> None:
        self.client_factory = client_factory
        self.lock = asyncio.Lock()
        self._last_start = float("-inf")

    async def _metadata(self, client: Any, filename: str, channel: int) -> tuple[int, str]:
        metadata = await client.request(FILE_METADATA, "PUT", data={
            "filetype": 1, "filename": filename, "digesttype": 1,
            "channel": channel, "packetsize": 1024, "windowsize": 1024,
        })
        # A successful metadata request reserves a device-wide transfer channel.
        # Closing the instruction connection DOES NOT release it. GS006's host
        # service maps nonzero finish codes to cancel_transfer(channel), which
        # releases the download without pretending its contents were received.
        # Never cancel on a rejected download: that channel may belong to Studio.
        try:
            return _signature(metadata, filename)
        finally:
            await client.request(FILE_FINISH, "PUT", data={
                "channel": channel, "code": -1,
                "message": "metadata inspection complete",
            })

    async def run(self, *, prepare_only: bool = False) -> None:
        """Re-arm the same cached job; prepare_only is for no-laser validation."""
        if self.lock.locked() or time.monotonic() - self._last_start < 5:
            raise RepeatJobError("Repeat was already requested; no additional run was queued.")
        async with self.lock:
            async with self.client_factory() as client:
                identity = await client.request("/v1/device/machineInfo")
                if (identity.get("deviceCode") != "GS006" or identity.get("firmware", {})
                        .get("package_version") not in SUPPORTED_FIRMWARE):
                    raise RepeatJobError(
                        "Repeat supports only regular F2 (GS006), firmware "
                        + " or ".join(sorted(SUPPORTED_FIRMWARE)) + "."
                    )
                _check_idle(await client.request(RUNTIME))
                signature = await self._metadata(client, "tmp.xf", 210)
                if signature != await self._metadata(client, "last.xf", 211):
                    raise RepeatJobError("The uploaded job differs from the last job. Repeat was refused.")
                _check_idle(await client.request(RUNTIME))
                await client.request(PREPARE, "PUT", data={
                    "fileType": "xf", "autoStart": 0, "loopPrint": 0,
                })
                deadline = time.monotonic() + 4
                while True:
                    runtime = await client.request(RUNTIME)
                    _check_alarm(runtime)
                    mode = runtime.get("curMode", {})
                    if mode.get("mode") == "Work" and mode.get("subMode") == "workReady":
                        break
                    if mode.get("mode") not in IDLE_MODES or time.monotonic() >= deadline:
                        raise RepeatJobError("F2 did not become ready; no start was sent.")
                    await asyncio.sleep(0.25)
                if prepare_only:
                    return
                # Detect a concurrent Studio upload before the explicit start.
                if signature != await self._metadata(client, "tmp.xf", 212):
                    raise RepeatJobError("The cached job changed during preparation; no start was sent.")
                runtime = await client.request(RUNTIME)
                _check_alarm(runtime)
                mode = runtime.get("curMode", {})
                if mode.get("mode") != "Work" or mode.get("subMode") != "workReady":
                    raise RepeatJobError("F2 state changed during preparation; no start was sent.")
                self._last_start = time.monotonic()
                try:
                    await client.request(START, "PUT", params={"action": "start"}, data={})
                except Exception as err:
                    raise RepeatJobError(
                        "Start was sent but its result is uncertain. Check the F2 before trying again; no retry was sent."
                    ) from err
