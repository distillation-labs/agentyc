# Installation and rollback preflight

This repository has a **macOS-first test-only preflight**, not a production installer. Linux and Windows are currently unsupported by this drill. The script never launches Chrome, downloads Chrome, opens Chrome UI, installs an extension, or changes a Chrome profile during a preflight.

## Safe default

Run the read-only preflight with no action flag:

```bash
python3 scripts/run_install_drill.py \
  --clean-profile \
  --artifact-dir artifacts/p0-installation
```

`--clean-profile` is a required-drill request. The path defaults to
`artifacts/p0-installation/clean-profile`; pass a path when needed, but it must
be inside `--artifact-dir`. The path may be absent or empty. The script only
checks it and never creates, cleans, or populates it.

The artifact directory must be inside the repository `artifacts/` directory,
may not be a symlink, and is the only location where the report and drill
record can be written. A required preflight exits nonzero unless actual
installation and rollback evidence exists. This is intentional: a clean local
fixture or a detected Chrome executable is not proof that an extension loaded,
Native Messaging connected, or rollback preserved the user's browser state.

To inspect static inputs without making the run required:

```bash
python3 scripts/run_install_drill.py --artifact-dir artifacts/p0-installation
```

The report is `artifacts/p0-installation/report.json`. It contains statuses,
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
user-approved Chrome run. If a different manifest is already present, the
script refuses to replace it. Use `--install` or `--rollback` separately only
for an explicit, operator-controlled action:

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
Rollback refuses to delete a changed or unowned manifest. A pre-existing exact
manifest is reported as already installed and is not removed by the drill.

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
