# Native HP plugin packaging (operator only)

Customers install the platform package, not the GitHub source archive. The native
`bin/hp` embeds Python and certificate data. The desktop app supplies its already
installed Codex runtime; no second CLI is installed. Current real-host validation
targets macOS Apple Silicon. Other platforms need their own build and acceptance.

Build in an isolated environment with `requirements-build.lock` and Python 3.13:

```sh
uv venv output/build-env --python 3.13
uv pip sync --python output/build-env/bin/python --require-hashes tools/requirements-build.lock
output/build-env/bin/python tools/build_desktop_plugin.py --output output/native-release
```

The output contains the installable ZIP, an unpacked marketplace/plugin, source
hashes, binary/archive hashes, and build-process ownership records. The source
snapshot copied into staging is the one compiled. Keep task output uncommitted.

Before general distribution, verify the actual plugin installation preserves the
executable mode and uses the right architecture, signing/notarization as required
by the distribution channel, existing HP login reuse, and private tool metadata.
Do not bypass operating-system security checks or ask the customer to install a runtime as a workaround.
Register the conversation app in the customer's own ChatGPT account; personal
app IDs and auth tokens never belong in the public package. Preserve unrelated
Codex settings. Do not claim a fresh-account install passed based on an already
configured developer account.

The compatible HP service must enable `direct_transfer_enabled` on the site's
server-owned configuration. Deploy the service before distributing this plugin.
Snapshots created before enablement have no direct-download artifact: preserve
pending local work and reopen/reconcile through the existing workspace recovery
procedure. Never discard an uncertain save merely to reopen a workspace.

Test stages separately: local edits make no dev/prod changes; a user's local
acceptance permits dev preparation; their later exact-artifact click permits
production. A source-level or HTTP test is not a browser interaction test, nor a
live S3 integration or a fresh-machine installation test.

## OpenAI submission candidate

The public submission ZIP has `plugin.json`, `mcp.json`, `skills/`, `assets/`, and
`bin/` at its root. Do not upload the marketplace-wrapper ZIP to the portal.

```sh
python tools/build_submission.py --native-package output/native-release/package --output output/submission-candidate
```

This builder reuses only an exact verified binary and checks that its embedded
runtime sources still match. Listing metadata can be updated separately. A
successful build is not portal validation or human-review submission. Current
review-account credentials and the accessible video must be supplied privately;
never package a real user's credentials or invent passed review tests.
