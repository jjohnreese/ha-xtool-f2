# Offline investigation: F2 firmware 40.56.025.00.ht4

Prepared on 2026-11-12 before live testing. The unpublished local build
was called 2.8.4b1; it subsequently passed pairing and two consecutive
physical repeats. It is carried forward into 2.9.0b1. The sections below
preserve the original offline findings; current validation is in the
[F2 firmware and controls guide](F2-firmware-and-controls.md).

## Findings

The public F2 catalog version `40.56.025.04` contains
`xTool_GS006_40.56.025.00.ht4.zip` and requires Studio 1.10.8 or newer.
Catalog version and installed package version use different suffixes.
The published notes mention flame detection optimization and bug fixes;
they do not describe the authentication change.

Offline comparison of the old ht3 and new ht4 `libmk-host.so` confirms
that ht4 introduces `/v1/user/auth-status`, a permanent token manager,
per-connection `sessionToken` storage/expiry, and instruction-request
validation of that session token. The ht4 code returns `406` with
`WAITING_BUTTON_CONFIRM` / `need button confirm` for initial pairing and
`403` / `session token invalid or expired` when request authentication fails.
The ht3 library lacks this new token-authentication implementation.

The default fork branch at `23a2b47` (2.8.3) uses the earlier guest-only
handshake and discards the parity response, so it cannot send session
tokens. This is a concrete firmware incompatibility consistent with the
reported unavailable controls. No response was obtained from the owner's
running F2, so the exact live rejection is not confirmed.

Repeat also had a separate old-firmware allowlist and a separate temporary
guest-only connection. Fixing only the main connection would leave Repeat
unusable on ht4.

## Candidate changes

- Bring `fix/v2-pairing-token` commits `0a6768a` and `7f5a4e6` into the
  latest `f2-repeat-job` code, preserving its pause mapping, repeat-channel
  cleanup, translations and wireless-button examples.
- Persist permanent pairing tokens through Home Assistant config entries;
  use a fresh session token for each connection. Request reauthentication
  when pairing is required. Button-confirmation waiting occurs only in the
  explicit pairing flow.
- Include the session token on heartbeat requests as well as ordinary
  requests. Close a connection when its heartbeat is rejected. Clear the
  session token on disconnect while retaining the saved pairing token.
- Give each temporary Repeat connection the current saved pairing token,
  use its own returned session token, and persist any token rotation back
  to the main client/config entry. Do not automatically pair or retry a
  rejected repeat command.
- Enable exact ht4 package version as a candidate alongside ht3. Retain
  GS006-only identification, idle/alarm and metadata checks, explicit
  transfer cleanup, disabled auto-start/looping, and one start without retry.
- Avoid raw frame dumps and redact pairing/session tokens in protocol logs.

The new firmware still exposes the cached-job preparation fields
`autoStart` / `loopPrint` and the transfer-finish code distinguishes success
from cancellation. These static findings do not establish physical repeat
behavior on ht4.

## Validation and limits

Offline tests exercise the actual protocol/client code with simulated
responses; opening a real aiohttp session is forbidden in those tests.
All **37 tests pass**: 21 repeat-controller tests and 16 protocol/pairing
tests. Python 3.12 compilation of the integration passes.
They cover saved-token handshake/rotation, explicit pairing, blocked and
timed-out pairing, legacy fallback, authenticated request/heartbeat frames,
rejected heartbeat handling, token cleanup/redaction, pause mapping,
Repeat token wiring, authentication refusal and existing repeat safeguards.

At the initial offline stage, Home Assistant was not installed in the local test environment. Compilation
is checked with Python 3.12, but full HA setup/config-flow execution,
Marketplace/HACS download, pairing on F2, restored controls and consecutive
physical repeats remain unverified. No machine probes, pairing, control
commands, integration reloads, Home Assistant restarts or deployment were
performed during that offline investigation. Live validation was performed
later, only after the owner made the machine available for testing.

When the owner makes the machine available for testing, first validate
installation and pairing, then live status/controls, and finally supervised
same-job repeats including a second completed cycle. Do not advertise ht4
as hardware-tested until those results are observed.

## Sources

- [Default branch reviewed at 23a2b47](https://github.com/jjohnreese/ha-xtool-f2/tree/23a2b47)
- [Existing pairing implementation](https://github.com/jjohnreese/ha-xtool-f2/tree/fix/v2-pairing-token)
- [Public F2 firmware package analyzed offline](https://storage.atomm.com/efficacy/atomm-package/prod/packages/261/75c4c704-7fd8-4136-84e1-59b85c66a7ae/xTool_GS006_40.56.025.00.ht4.zip)
- [xTool public package catalog endpoint](https://api.xtool.com/efficacy/v1/package/version/latest), queried for `xTool-f2-firmware`.

Only the selected application archive was range-downloaded and CRC-checked;
the complete firmware ZIP was not downloaded or installed. No firmware
binary was executed. Local research binaries are excluded from the repo.
