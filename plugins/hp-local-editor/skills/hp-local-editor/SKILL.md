---
name: hp-local-editor
description: Edit a managed static website locally, preview file changes immediately, and save or confirm verified site updates through HP local editor. No customer Git installation or GitHub account is required.
---

# Local homepage editing

Use the bundled Python 3 tools on macOS or Linux; Windows is not supported.
The package helper `hp_local_editor.py` never invokes Git, installs dependencies,
runs supplied scripts, or makes network requests. The separate `hp_preview.py`
launcher serves a loopback preview and uses the installed Codex app-server for
allowlisted HP tools without starting a model turn or reading credentials.
Remote operations use the authenticated HP service below. Read [the wire contract](references/protocol.md) when
integrating a service or diagnosing rejected packages.

## Plugin setup

For a setup request, confirm macOS/Linux and Python 3, then discover the plugin's
HP control tools. The configured endpoint is `https://hp-control.simy.one/mcp`.
Installing this package is not evidence of a working connection. Add only the
requested plugin settings, preserving existing configuration; do not invent an
app registration or claim OAuth setup succeeded. The host must discover
protected-resource/OAuth metadata, complete
Google login in the user's own UI, and verify `hp_profile` plus `hp_list_sites`.
The server selects the validated Google subject and site roles; never supply a
subject or repository as a tool argument.

Connect the Google Drive plugin separately for snapshot download and changes-ZIP
upload. Read its actual connected profile and compare its verified account email
to `hp_profile.email`; compare the Google subject too when the connector exposes
the same identifier (a Drive permission ID is not an OIDC subject). An email alias
is not a separate Google account. If the account binding cannot be established,
stop before transferring files. Use exactly `upload_folder_id` from the
authenticated completed open-workspace `result`; do not substitute a pasted or
guessed folder, search for a similar name, or widen Drive permissions. Missing access or setup is a concrete
setup gap, not a reason to fall back to the old spreadsheet intake skill.

Before claiming support on a Codex host, verify OAuth discovery/DCR, private
result metadata and app-only confirmation there. If a host needs a registered
MCP mapping, use its actual approved ID and keep personal registrations out of
the public package. Draft saving and publication are separate; service gates and
the user's exact-artifact confirmation govern publication.
A setup-only request does not open a workspace, save a draft, or publish.

## Immediate local preview and two confirmation surfaces

For local display or floating controls, read [local preview](references/local-preview.md).
Launch the installed `scripts/hp_preview.py` with the existing validated workspace.
Open the exact printed loopback URL; edits under `site/` reload automatically.
The local shell keeps controls separate from the sandboxed site. Do not inject
publication controls or credentials into site files. Local preview requires no
cloud save or deployment.

The conversation card and local confirmation dialog use the same server approval
and operation records. Use the supported surface the user chooses; do not claim a
Codex conversation card works based on a local-browser test. When publication is
disabled, the confirmation is explicitly a connection test with no publication.
A local confirmation click is a browser-host test, not Codex MCP Apps acceptance.

## Control tools and local receipts

Discover the actual tool handles; these are their service-level names:

| Purpose | Tool |
| --- | --- |
| Verify authenticated identity and sites | `hp_profile`, `hp_list_sites` |
| Request an editable snapshot | `hp_open_workspace` |
| Read the durable operation result | `hp_get_operation` |
| Save an uploaded changes ZIP | `hp_save_draft` |
| Prepare a verified preview | `hp_submit_preview` |
| Request cancellation | `hp_cancel_operation` |
| Resume an eligible paused execution after its cause is fixed | `hp_resume_operation` |
| Open a publication confirmation card | `hp_prepare_publish` |
| Recover the original confirmation result without a nonce | `hp_get_confirmation` |
| Withdraw an unaccepted confirmation without cancelling a publication | `hp_cancel_confirmation` |
| Submit the private card's confirmation | `hp_confirm_publish` (app only) |

Keep operation IDs stable on retries. For `hp_save_draft`, use exactly the ID
returned by local `pack`, not a newly generated ID. The input bundle hash is the
helper's `bundle_sha256` and the file ID is the uploaded ZIP in that exact
open-result `upload_folder_id`. A queued/running operation has not saved or published yet.

Keep public control requests and results in a local `control-receipts/` directory
beside the site workspace so a new Codex session can recover the same operation
IDs. Keep these records outside `site/`; do not copy OAuth tokens, signed download
URLs, or private confirmation metadata into them.

For an open-workspace operation, read its `result` only after authenticated
completion and save that exact `kind: "workspace"` object as `open-result.json`.
Download exactly its `snapshot_file_id`, then validate the snapshot against that
receipt using `init` below. The helper checks the ZIP
`snapshot_sha256` plus manifest `site_id`, `workspace_id`, and `base_commit` before
creating any workspace or lock metadata. A self-consistent ZIP is insufficient
without the authenticated server result. Preserve that result if download or
validation fails; do not invent a receipt or use a different workspace's result. For a saved draft, extract the public
operation's `result` object (not the whole tool or operation envelope) as the
saved receipt: `schema_version`, `kind: "saved"`, `site_id`, `workspace_id`,
`operation_id`, `base_commit`, `saved_commit`, `bundle_sha256`. Pass that exact
receipt to `accept`; all identity/base/hash fields must match the pending package.
Reporting/notification problems do not authorize resending a save or publication.

Commands below are relative to this installed skill directory; use its actual
absolute helper path when the user's site workspace is elsewhere.

Initialize a new dedicated workspace from a service-provided snapshot:

```sh
python3 scripts/hp_local_editor.py init snapshot.zip workspace --receipt open-result.json
```

Edit ordinary static files under `workspace/site/`. Keep `.hp/` private and
unchanged: it holds the authenticated open-result copy, validated baseline,
pending candidate, and saved receipts.
Use the customer's requested scope, inspect the site in a browser, and explain
visible changes. Never execute downloaded JavaScript, hooks, or build scripts as
part of package initialization. Browser preview of a site is a separate explicit
verification activity with the usual browser isolation.

Inspect changes, including a text diff or binary-file notice:

Write a concise change summary from the actual diff and the user's request;
pass it to `pack --summary`. Do not copy the whole conversation, email addresses
or credentials into it. The server adds trusted operation metadata and supplies
a deterministic fallback title if the summary is absent. A title never authorizes
publication. During an authorized editing task, save a draft after each coherent
group of edits and before ending the work; setup-only or discussion-only requests
do not authorize draft writes. Report saved status only after the exact receipt
has been read back and accepted locally.

```sh
python3 scripts/hp_local_editor.py status workspace --diff
python3 scripts/hp_local_editor.py pack workspace changes.zip --summary 'Update opening hours'
```

The output ZIP must be outside the workspace. Existing output files are accepted
only when their bytes exactly match the pending bundle; choose a new filename for
a different package. No-op packing produces no ZIP.
Deletions are explicit. A pending operation is immutable: retries reuse its exact
bytes and operation ID even when local files have changed since packing. Retry
without a new summary. Do not remove pending metadata to bypass an ambiguous
save; first reconcile the operation with the service.

Send the package only through the configured local editor service after target
and authorization are established. Set `hp_save_draft.operation_id` to the exact
`operation_id` returned by `pack`; generating a fresh UUID would contradict the
bundle and be rejected. A save receipt acknowledges that exact
package, not later edits. After receiving the matching saved receipt:

```sh
python3 scripts/hp_local_editor.py accept workspace saved-receipt.json
python3 scripts/hp_local_editor.py status workspace --diff
```

Acceptance advances the baseline to the saved pending candidate and preserves
all later edits under `site/`. Duplicate acceptance is safe. A receipt mismatch,
unsafe path, baseline mismatch, or server conflict stops that save: explain the
specific error and reconcile with the service; do not invent a successful save.
The helper checks consistency, not server authenticity: receipts and snapshots
must come from the trusted authenticated service.

Saving a draft does not publish. The server owns Google authentication, repository
binding, preview/reconciliation, approval, and publication controls. No backend
AI is needed; content changes and conflict decisions remain in the customer's
Codex conversation. The repository retains `hp-customer-intake` separately for
compatibility. It is not bundled into this plugin and is not an implicit fallback.

Each helper command takes a nonblocking exclusive workspace lock (including
status). A busy workspace fails immediately; retry after the active invocation
finishes. The persistent sibling `.WORKSPACE.hp.lock` file is coordination
metadata, not a site file. Do not remove it while a helper might be running.

When the authenticated service definitively rejects a save with no external
save effect (for example, a stale workspace), keep the old workspace, pending
bundle, and rejection evidence. Open a **new** workspace from the latest server
snapshot in a different directory, then have Codex inspect the old/local and new
files and manually reapply only the desired changes. Inspect and verify the new
site before packing with its new baseline and operation ID. Never delete
`.hp/pending` to bypass recovery. If the save outcome is uncertain, reconcile the
old operation first; a new workspace is not proof that the prior save had no effect.

For an execution in `needs_attention`, first establish that the reported cause
has been fixed and the user still wants that same operation completed. If the
service exposes `hp_resume_operation`, pass the original `operation_id` and one
new `recovery_id` UUID; save that request before sending it and reuse the recovery
ID if its response is lost. This retains the original input, effects and any
prior exact-artifact approval. It does not authorize a new publication or change
cancelled work. Read the original operation until its factual outcome is known.
Do not loop through new recovery IDs while the cause remains unresolved, edit
the service ledger, or use execution resume to resend a completed publication's
email. Reporting-only problems need reporting recovery, not another publication.

If confirmation's response is lost, preserve `approval_id` and read
`hp_get_confirmation` to recover the existing operation or connection-test result.
`prepared` or an expired deadline alone cannot prove that an in-flight confirmation
had no effect. An explicit `hp_cancel_confirmation` withdraws an unaccepted card
atomically; it returns any already accepted publication unchanged. A matching
`already_requested` result can contain a failed or cancelled operation: display its
actual status. Never resend a confirmation nonce or turn a test card into approval.
