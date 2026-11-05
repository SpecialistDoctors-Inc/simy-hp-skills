# HP local workspace protocol v1

The customer edits ordinary files locally. Only the server accesses GitHub. The
operator identity, repo, branches, Drive roots, recipient and deployment targets
come from server-owned config. Actors are validated Google subjects, never inputs.

## Snapshot

A ZIP contains `manifest.json` and `files/<path>` for every editable static file.
Manifest: `schema_version: 1`, `kind: "snapshot"`, `site_id`, `workspace_id` (UUID),
`base_commit` (40 lowercase hex), and sorted `files` array of
`{path, sha256, size}`. Paths are relative to the site's `site/` directory.
The server owns the authoritative snapshot/manifest. The client stores metadata
and base content in `.hp/` outside the editable files directory.

## Authenticated open-workspace result

The completed authenticated operation returns its `result` as
`{schema_version:1, kind:"workspace", site_id, workspace_id, base_commit,
snapshot_file_id, snapshot_sha256, download_url, upload_folder_id}`.
The local `init snapshot.zip workspace --receipt open-result.json` requires this
exact result: recompute the ZIP hash against `snapshot_sha256` and compare its
manifest site/workspace/base to the result before creating output or lock files.
An internally valid ZIP cannot establish its own provenance. The local helper
checks consistency; authenticated receipt origin is the host/service boundary.
The original result is preserved under `.hp/open-receipt.json`.

Download only the result's `snapshot_file_id`; upload a changes ZIP only to its
`upload_folder_id`. Compare the real connected Drive profile with `hp_profile`
before transfer. A pasted folder identifier or editable document is not authority.

## Changes

A ZIP contains `manifest.json` and only added/modified `files/<path>` bytes.
Manifest: `schema_version: 1`, `kind: "changes"`, `site_id`, `workspace_id`,
`base_commit`, `operation_id` (UUID), `change_summary` (optional, max 200 chars),
and sorted `changes` array of `{path, action, base_sha256, sha256, size}`.
Actions: `add` (base_sha256=null), `modify` (base_sha256 set), `delete`
(base_sha256 set, sha256=null, size=0, no ZIP payload). No extra fields or payloads.
The claimed hashes and base must match recomputed bytes and the server-held base.
No changes means no external save effect. Stable operation_id is reused on retry;
same ID with different bytes/summary is rejected. The summary is descriptive only,
never authority. Server adds trusted request metadata to a single-line commit title.

## Receipt

Saved operation result: `{schema_version:1, kind:"saved", site_id, workspace_id,
operation_id, base_commit, saved_commit, bundle_sha256}`. It acknowledges exactly
the submitted package, not any newer edits. Client keeps original pending bytes,
updates its base to the acknowledged pending candidate, and preserves later edits.

## Paths and limits

ZIP transport max 60 MiB; extracted editable bytes max 50 MiB; one file max 10 MiB;
max 1000 files. Reject symlinks/special files, duplicate names, absolute or parent
paths, backslashes, colons, NUL, non-NFC and case-folding collisions. Deny hidden
components except `.well-known`; deny `.hp`, `.git`, `.github`, credentials/configs,
node_modules and server/build scripts. Allowed file suffixes: .html .css .js .json
.png .jpg .jpeg .webp .gif .svg .ico .txt .xml .woff .woff2 .ttf .pdf .webmanifest.
Root index.html is required for a snapshot/candidate. Transport entries must be
regular files or safe directory entries. Never invoke git, shell hooks, dependencies
or supplied executables from the package. Local Python helper uses stdlib only.

## Server reconciliation

Save-draft advances only the caller's server-owned workspace branch, using the
expected draft head (base_commit); no last-writer-wins. Preview compares workspace
base-on-dev B, candidate U, latest-dev R. Per file: if U==B retain R; if R==B apply U;
if U==R retain it; otherwise return a conflict, including delete/modify. Unrelated
file edits combine. Conflicts return to customer Codex; no server AI resolves them.
A new preview revokes earlier publication preparation for the request. Public
artifacts and approval bind the final resulting tree, not the user's initial ZIP.
