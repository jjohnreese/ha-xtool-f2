# Regular F2: firmware, installation and controls

This guide is for **xTool F2 GS006**. F2 Ultra, F2 Ultra UV and other xTool
models have different firmware and are not covered by the F2 hardware tests.

## Choose the release for your firmware

| Installed F2 package | xTool catalog version | Integration | Validation |
|---|---|---|---|
| `40.56.024.00.ht3` | `40.56.024.03` | [v2.8.3 legacy](https://github.com/jjohnreese/ha-xtool-f2/releases/tag/v2.8.3) | Preserved previous build; original tests and limits remain in its release notes |
| `40.56.025.00.ht4` | `40.56.025.04` | [v2.9.0b1](https://github.com/jjohnreese/ha-xtool-f2/releases/tag/v2.9.0b1) | Pairing/authenticated connection and two consecutive same-job repeats confirmed on one F2 |
| Any other version | — | Unverified | Repeat refuses versions outside its exact allowlist |

The package version is what the machine reports; the catalog version is
the update listing. Do not flash firmware merely to make those suffixes
match. xTool lists Studio **1.10.8 or newer** for the ht4 package.

v2.8.3 stays available at its original tag and commit. Do not use that
guest-only build with ht4: the new firmware requires pairing and a session
token. v2.9.0b1 retains the ht3 repeat allowlist and legacy handshake
fallback, but it has not been physically revalidated on ht3. Use v2.8.3
when you need the preserved older-firmware build.

## Install through HACS or Marketplace

1. Back up `/config/custom_components/xtool` and note the current release.
2. Open HACS, or Marketplace on Home Assistant versions that include it.
   Read and accept its own first-use warning if requested.
3. Add custom repository `https://github.com/jjohnreese/ha-xtool-f2`,
   category **Integration**, if it is not already registered.
4. Open **xTool Laser** and select the matching version from the table.
   Enable beta/prerelease versions if needed to see `v2.9.0b1`.
5. Download, then restart Home Assistant to load the changed Python code.
6. Keep the existing xTool integration and device. Do not delete and re-add
   them; existing entity IDs and automation references should stay intact.
7. For ht4, finish pairing as described below, then check the device page.

A manual install copies the release's `custom_components/xtool` directory
to `/config/custom_components/xtool`, followed by the same restart and
pairing checks. Do not mix files from different versions.

## Pairing on ht4

The F2 must be idle and attended. Disconnect Studio while establishing
the connection. Home Assistant can show a reauthentication repair; open
it and follow the pairing prompt. You can also use the integration's
**Reconfigure** action and choose **Pair again** without changing its IP.

Submit the confirmation form. Approve the connection on the F2 **only
when it requests connection confirmation**. Do not press Start or Repeat
to pair. Wait until the integration loads and the Status sensor reports
the machine's live state.

The integration saves the permanent pairing token privately in its config
entry. Every connection obtains its own session token, including the
temporary Repeat connection. Tokens are not configuration examples to
copy into YAML, logs, issues or this repository.

`503: device is busy` means the F2 refused the pairing attempt. Let any
job finish, disconnect Studio, and retry the pairing form. Do not keep
pressing the machine button without a confirmation request. Once the
integration is paired and loaded, close any leftover pairing form instead
of starting another pairing flow.

## What the controls and readings mean

| Entity | Behavior |
|---|---|
| Status | Live state such as idle, processing ready, processing, paused or finished. The 2.8.3 pause fix is preserved. |
| Start / Resume job | Starts a prepared job or resumes a paused job. It does not re-arm a completed retained job. |
| Pause job | Requests a pause; the Status sensor should change to paused. |
| Cancel job | Stops the current job. No live cancellation test was performed in the ht4 validation session. |
| Repeat last job | Checks the retained matching job, prepares it with auto-start and looping disabled, then sends exactly one Start. |
| Exhaust fan switch | Requests exhaust on/off. This is separate from job start. |
| Exhaust fan binary sensor | Follows the F2 exhaust output's reported `ext_purifier.state`, including automatic changes, rather than remembering the last manual command. It is not a fan RPM measurement or an independent air-purifier sensor. |
| Fill light front/back | Controls the two enclosure lights; the actual device brightness is reported back. |
| Alarm binary sensor | Current generic problem indicator. An Error/Fire warning event is not its on/off equivalent. |
| Error event | Timestamp and type of the last error event received. `unknown` means no event timestamp is available, not that an error is active. |
| Fire warning event | Timestamp and type of the last fire-warning event received. `unknown` does not mean fire detection is disabled or that an active fire was detected. |
| Device sleep | Not created for regular F2: its firmware rejects `autoSleepEnable` on and off. Other supported models retain their own sleep capability. |

Event entities represent occurrences, not continuous on/off states. Do not
fabricate a cleared/error event at startup merely to replace `unknown`.
Use the timestamp together with `event_type`; an attribute without a valid
event timestamp is not evidence of a newly received event. See the
[Home Assistant Event documentation](https://www.home-assistant.io/integrations/event/).

If an older installation retains the unsupported Device sleep entry,
disable it under **Settings → Devices & services → Entities**. This hides
the unusable control while preserving its registry record. Removing the
control does not put the machine to sleep or change a safety setting.

## Repeat safeguards and troubleshooting

- Use only while supervising the F2 with the correct material positioned.
- Finish the current run before pressing Repeat again. Busy, paused,
  sleeping, unknown or alarmed states are refused; another job is not queued.
- `tmp.xf` and `last.xf` must have matching nonempty size/digest metadata.
  If they do not match, transfer and complete the intended job through
  Studio before trying Repeat.
- Each successful metadata reservation is explicitly released. A rejected
  channel is never cancelled because it might belong to another client.
- A timeout after Start is uncertain: the job may already be running.
  No automatic Start retry is sent; inspect the machine before another press.
- Single press on Start / Resume is not a physical double tap. Use the
  separate Repeat entity or the documented wireless-button double press.
- Studio can interrupt a connection or change the cached job. Avoid
  uploading another design during Repeat. Session-token authentication is
  required for ordinary requests and heartbeat messages on ht4.
- If controls are unavailable after an update, verify the package version,
  matching release, completed restart and pairing before testing a job.

## Validation and boundaries

On **2026-11-12**, one regular F2 on **40.56.025.00.ht4** and Home Assistant
**2026.11.1** completed pairing and reported its live firmware/status.
The owner exercised Start, Pause and Resume; corresponding state changes
were recorded. Two consecutive separate Repeat presses were physically
confirmed to run the same job once each, and both completions were recorded.

The repeat/authentication path was tested as the local 2.8.4b1 candidate.
v2.9.0b1 adds the fan-state mapping and removes unsupported sleep controls.
The release notes distinguish the post-install checks from those earlier
physical tests. Offline tests cover authentication, repeat safeguards, pause
mapping and the fan parser. No fire or other physical fault was induced;
alarm-event reception has not been physically validated. Other models,
firmware versions, job types and accessories were not comprehensively tested.

## Roll back

For an older-firmware F2, select and download the retained **v2.8.3** release
through HACS/Marketplace, then restart Home Assistant. Keep the integration
entry and existing entities. Alternatively restore the saved component
directory and restart. A component-file rollback does not roll back the
machine firmware or delete saved pairing credentials.

For an ht4 machine, v2.8.3 is not an authentication-compatible rollback.
Restore a known working ht4-capable component backup instead. Do not
downgrade the machine firmware as part of an integration rollback.

To undo disabling an old Device sleep registry entry, re-enable that same
entity and reload a build/model that actually provides it. On ht4 the old
control will still be rejected, so keep it disabled.
