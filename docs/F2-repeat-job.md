# Separate Repeat last job button (regular F2 hardware tested)

This fork of [thecodingdad/ha-xtool](https://github.com/thecodingdad/ha-xtool)
adds **Repeat last job** beside the existing Start / Resume button.
Only the **regular F2, device code GS006** is enabled for this feature.
Firmware **40.56.024.00.ht3** has the physical validation described below;
**40.56.025.00.ht4** has two consecutive physical repeat confirmations
from the owner on 2026-11-12, after pairing with the new protocol.
See [firmware compatibility and controls](F2-firmware-and-controls.md) and
[the ht4 investigation](F2-ht4-investigation.md).
F2 Ultra and F2 Ultra UV are different machines.
The original MIT license and upstream attribution are retained.

## What a press does

1. Open a temporary local TLS WebSocket connection to the configured device.
   Present the integration's saved pairing token if present, then use the
   session token issued for this connection. Pairing is handled separately
   through Home Assistant; pressing Repeat never waits for a pairing press.
2. Verify the actual model, firmware, idle/finished state and alarm status.
3. Check that retained `tmp.xf` and `last.xf` have matching nonempty sizes
   and MD5 fingerprints, using file metadata without downloading the design.
   Explicitly cancel each metadata transfer with a nonzero finish code to
   release its device-wide channel before continuing.
4. Recheck the state and send `PUT /v1/processing/upload/config` with
   `{"fileType":"xf","autoStart":0,"loopPrint":0}` once.
5. Wait for `Work / workReady`; recheck the upload fingerprint and status.
6. Send `PUT /v1/processing/state?action=start` exactly once, then close
   the temporary connection. Closing alone does not release transfer channels;
   each one was already cancelled. No run is queued and no command is retried.

An overlap or another press within five seconds of the start request is
refused. Missing/different cached jobs, busy/paused states, alarms,
unrecognized firmware, and uncertain responses stop the sequence.
If a request times out after start was sent, inspect the machine before
trying again: it may already be running.

Stand beside the machine with the enclosure closed and the correct
material positioned before pressing Repeat. Do not upload another job
from Studio while repeating. This feature does not change safety settings
or reproduce a physical double tap. It re-prepares the existing cached
file, then uses the existing start command. There is no timer or automatic
repeat on job completion, startup or reconnect.

## Validation status

- 2026-11-12, regular F2 GS006, firmware **40.56.025.00.ht4**, Home
  Assistant **2026.11.1**: the owner approved pairing. Live firmware and
  status returned; Home Assistant recorded Start, Pause, Resume and Finish.
- The owner confirmed **two consecutive Repeat presses each ran the same
  job once**. Home Assistant recorded both starts and finishes. The second
  press did not reproduce the former `channel is busy` failure.
- This used the local 2.8.4b1 authentication candidate; 2.9.0b1 carries
  the same repeat/authentication behavior plus exhaust-state reporting and
  removal of the unsupported Device sleep control. Post-publication
  Marketplace/HACS verification is tracked in the new-release notes.
- Error/fire handling has not been tested by inducing a physical fault or
  fire. No such test is required to make an event entity show a timestamp.

### Earlier ht3 history

- 2026-11-04: existing Start / Resume works, confirmed by the owner on a regular F2.
- 2026-11-04: cached file metadata matched; preparation returned success
  with auto-start disabled; the device became `Work / workReady`.
- 2026-11-04: the owner manually pressed the existing Start / Resume
  button and confirmed the same job ran again once.
- 2026-11-04: the six candidate files were installed with an original-component
  backup. Home Assistant restarted, the xTool config entry loaded, and the
  separate Repeat last job entity appeared. No xTool errors were reported
  by Home Assistant's system log at the post-start check.
- Python 3.12 compilation and all 19 repeat regression tests pass.
- 2026-11-04: the owner pressed the new combined Repeat last job entity
  and confirmed that **the same job ran exactly once**. Home Assistant
  recorded the button press, a subsequent `started` event, processing,
  and `finished` approximately 23 seconds later (reported job duration:
  22 seconds). No xTool system errors were reported during the test.
- Test environment: Home Assistant **2026.10.4**, regular F2 **GS006**,
  firmware **40.56.024.00.ht3**, local Wi-Fi. This is one physical machine
  and one combined-button test. Busy/alarm/hash-mismatch/duplicate guards
  are covered by software tests; they have not been provoked on the laser.
- A subsequent physical repeat exposed a channel leak in the initial
  implementation: the firmware returned `code -1`, `channel is busy`.
  Closing the instruction connection did not release the download channels.
- 2.8.2 explicitly sends `PUT /v1/filetransfer/finish` with
  `{"channel":<reserved channel>,"code":-1,"message":"metadata inspection complete"}`
  after each successful metadata request, including validation failures.
  The matching firmware routes a nonzero finish code to `cancel_transfer`.
  Rejected/busy channels are never cancelled, since another client may own them.
- 2026-11-04: nine consecutive metadata checks using the same three channels
  over three fresh connections succeeded with cleanup; the fingerprints
  stayed identical, the F2 stayed idle, and zero start commands were sent.
  **Consecutive physical repeats on 2.8.2 remain pending.**

## Local checks

```shell
python -m unittest discover -s tests -p test_f2_repeat.py -v
python -m unittest discover -s tests -p test_ws_v2_pairing.py -v
python -m compileall -q custom_components/xtool
```

The repeat regression tests use fake responses and cannot operate hardware.
The upstream accessory tests require a Home Assistant Python environment.

## Installation and rollback

Back up the current `/config/custom_components/xtool` directory. Replace
it with this fork's `custom_components/xtool`, then restart Home Assistant
to load the Python changes. Keep the existing xTool config entry; no
device deletion is needed. Newer firmware can require pairing through the
integration's reauthentication/reconfiguration flow. Do this only when the
machine is idle and attended. The new button is associated with
the same device; its unique-ID suffix is `repeat_last_job`.

For rollback, restore the directory backup and restart Home Assistant.
A HACS update from upstream can overwrite this fork's files. Use this fork
as the custom repository when installing this feature through HACS.

## Publication gate

The owner confirmed the combined button's same-job, single-run result
before publication. Only the model, firmware, Home Assistant version,
sanitized event evidence and test scope are documented here. Private
addresses, serials, credentials, cached design files and local research
artifacts are excluded from this repository.
