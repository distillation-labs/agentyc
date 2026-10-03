# Installation and rollback preflight

This repository has a **macOS-first test-only preflight**, not a production installer. Linux and Windows are currently unsupported by this drill. The script never launches Chrome, downloads Chrome, opens Chrome UI, installs an extension, or changes a Chrome profile during a preflight.

## Safe default

Run the read-only preflight with no action flag:

```bash
python3 scripts/run_install_drill.py \
  --clean-profile \
  --artifact-dir artifacts/p0-installation-preflight
```

`--clean-profile` is a required-drill request. The path defaults to
`artifacts/p0-installation-preflight/clean-profile`; pass a path when needed, but it must
be inside `--artifact-dir`. The path may be absent or empty. The script only
checks it and never creates, cleans, or populates it.

The preflight artifact directory is `artifacts/p0-installation-preflight/`; an
explicit installation/rollback drill uses the separate
`artifacts/p0-installation/` directory. Each artifact directory must be inside
the repository `artifacts/` directory, may not be a symlink, and is the only
location where that run's report and private drill record can be written. A required preflight exits nonzero unless actual
installation and rollback evidence exists. This is intentional: a clean local
fixture or a detected Chrome executable is not proof that an extension loaded,
Native Messaging connected, or rollback preserved the user's browser state.

To inspect static inputs without making the run required:

```bash
python3 scripts/run_install_drill.py --artifact-dir artifacts/p0-installation-preflight
```

The preflight report is `artifacts/p0-installation-preflight/report.json`. It contains statuses,
counts, byte sizes, and short SHA-256 prefixes only. It does not contain
absolute paths, extension IDs, browser/tab IDs, cookies, tokens, page bodies,
or screenshots.

## Explicit macOS drill

A Native Messaging registration is written only when an explicit action is selected:

```bash
python3 scripts/run_install_drill.py \
  --drill \
  --extension-id <32-character-chrome-extension-id> \
  --clean-profile \
  --artifact-dir artifacts/p0-installation
```

The ID is validated but is never written to the report. The drill:

1. checks the macOS platform, installed Chrome executable, test extension
   fixtures, executable test host, and clean-profile boundary;
2. writes the test host manifest to the macOS user-level Native Messaging path
   under `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/`;
3. verifies the exact manifest it wrote; and
4. removes only that exact manifest and its drill record.

It never launches Chrome or downloads anything. It does not install the
extension or claim that the extension UI, Native Messaging handshake, or
headed coexistence workflow passed. Those require a separately captured,
user-approved Chrome run. For branded Chrome, the default live unpacked-extension
validation lane uses the public trusted browser-target CDP Extensions domain:

```bash
python3 scripts/run_chrome_probe.py \
  --headed --require-live --launch-chrome \
  --artifact-dir artifacts/p0-extension
```

The runner launches an owned disposable Chrome window without
`--load-extension`, attaches only to that process's browser-target websocket,
calls `Extensions.loadUnpacked`, verifies `Extensions.getExtensions` identity,
path, version, and enabled state, runs the probe, calls `Extensions.uninstall`,
and verifies that the extension is absent before cleanup. It never calls
`chrome.developerPrivate`, inspects the internal extensions page DOM, or uses
file-picker APIs. For this disposable probe, the runner stages the test Native
Messaging manifest inside the owned profile; the separate user-level install
drill is not a prerequisite. The experimental CDP Extensions API is version-
gated, so the installed Chrome version is recorded in the artifact.

`--operator-assisted` remains an explicit diagnostic fallback for Chrome's
Developer mode + **Load unpacked** UI flow; it is not the automated release-gate
provenance. Use `--install` or `--rollback` separately only for an explicit,
operator-controlled action:

```bash
python3 scripts/run_install_drill.py --install \
  --extension-id <32-character-chrome-extension-id> \
  --clean-profile --artifact-dir artifacts/p0-installation

python3 scripts/run_install_drill.py --rollback \
  --extension-id <32-character-chrome-extension-id> \
  --clean-profile --artifact-dir artifacts/p0-installation
```

These actions can change only the explicitly targeted test Native Messaging
registration. They never mutate a Chrome profile, tabs, or user browser state.
The live disposable probe instead owns and removes its temporary profile and
stages its test registration there.
Rollback refuses to delete a changed or unowned manifest. A pre-existing exact
manifest is reported as already installed and is not removed by the drill.

## Lifecycle evidence record

A drill report always includes a complete, non-green lifecycle shape. Offline or
preflight values are `not_measured_offline`; they are not equivalent to a pass.
A real release artifact must include a separately captured, redacted record with
this shape:

```json
{
  "evidence_mode": "live",
  "lifecycle": {
    "schema_version": 1,
    "evidence_mode": "live",
    "install": "installed",
    "update": "passed",
    "uninstall": "passed",
    "downgrade": "passed",
    "rollback": "rolled_back"
  },
  "rollback_safety": {
    "schema_version": 1,
    "evidence_mode": "live",
    "new_mutations": "paused",
    "pages_retained": true,
    "user_tabs_preserved": true,
    "chrome_process_terminated": false,
    "global_close_used": false,
    "incompatible_ledger_refused": true,
    "kill_switch": {
      "status": "armed_and_verified",
      "armed": true,
      "verified": true
    }
  }
}
```

Validate this record with the explicit drill:

```bash
python3 scripts/run_install_drill.py \
  --drill --required --extension-id <extension-id> --clean-profile \
  --artifact-dir artifacts/p7-install-rollback \
  --lifecycle-record artifacts/p7-install-rollback/lifecycle-record.json
```

The record must be inside the selected artifact directory and must already be
stable after central redaction. The drill does not execute update, uninstall,
or downgrade; it rejects missing, skipped, offline, or unsafe lifecycle
claims. A lifecycle record cannot turn a failed local install/rollback into a
pass.

## Rollback safety and kill switch

Rollback is an ownership-bounded mutation. It may remove only the exact
registration payload and journal owned by the current drill. A changed,
symlinked, unreadable, incompatible, or stale ledger is refused; the unknown
file remains in place for operator reconciliation. Rollback never closes all
pages, terminates Chrome, changes a user tab, or performs a profile migration.

Before any direct-product mutation, the operator must arm and verify the
mutation kill switch. `armed_and_verified` is required evidence, and
`new_mutations: paused` is required while rollback is active. If the kill
switch cannot be verified, if page/user-tab preservation is unknown, or if an
incompatible ledger is detected, stop new mutations and keep the release
blocked. Do not override the refusal by deleting the registration manually.

For a non-user-mutating test, provide a registration path inside the artifact
directory:

```bash
python3 scripts/run_install_drill.py --drill \
  --extension-id <32-character-chrome-extension-id> \
  --registration-path artifacts/p0-installation/test-host.json \
  --clean-profile --artifact-dir artifacts/p0-installation
```

## Platform posture

| Platform | Drill status              | Registration posture                                          |
| -------- | ------------------------- | ------------------------------------------------------------- |
| macOS    | First supported platform  | User-level Chrome Native Messaging path; explicit action only |
| Linux    | Unsupported by this drill | No Linux path is inferred or modified                         |
| Windows  | Unsupported by this drill | No Windows registry/path is inferred or modified              |

Linux and Windows runs report `unsupported_platform`; a required run exits
nonzero. They must not be treated as successful installation evidence.

## Production Native Messaging registration

The production MV3 extension uses `com.agentyc.host` and the executable
`target/{debug,release}/agentyc-native-host`. Build the host, then install or
check the exact user-level registration without opening Chrome:

```bash
cargo build -p agentyc-host --bin agentyc-native-host --locked
python3 scripts/register_native_host.py \
  --install \
  --extension-id <32-character-stable-extension-id>

python3 scripts/register_native_host.py \
  --check \
  --extension-id <32-character-stable-extension-id>
```

The installer writes only the exact macOS Native Messaging manifest, uses an
atomic replacement, refuses a different existing manifest unless `--replace` is
explicit, and never launches Chrome or automates a file picker. The manifest's
`allowed_origins` is exact; wildcards are rejected. The host accepts Chrome's
transport origin from `argv[1]`, normalizing only its required trailing slash.
Direct same-user execution is not cryptographically distinguishable from a
Chrome-launched process; the OS-user threat boundary remains documented in
[`security/host-protocol.md`](security/host-protocol.md).

## Extension and rollback limits

The checked extension is the unpacked Phase 0 probe. Its unpacked ID is not
invented by the script; the operator must supply the exact ID for an explicit
drill. A stable production extension ID, signing, distribution, upgrade,
uninstall, Chrome UI installation, and profile binding are not established by
this repository yet.

The rollback proof covers only the test Native Messaging registration written
by the explicit drill. It does not prove extension uninstall, Chrome profile
rollback, tab restoration, or production downgrade safety. Those claims
require a real headed macOS run with a disposable profile, explicit user
approval, redacted logs, and evidence that unrelated tabs and the user's Chrome
process were unchanged.
