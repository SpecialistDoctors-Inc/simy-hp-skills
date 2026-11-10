---
name: hp-local-editor
description: Help a non-engineer edit a managed homepage by conversation. Prepare the workspace and open local preview automatically, then move to dev and production only after the user’s separate checks. No customer Git or additional runtime installation is required with the native package.
---

# Local homepage editing

Use the native distribution’s `bin/hp` from the installed plugin root. It includes
the runtime for its labelled OS/architecture. Windows is not supported.
The source archive alone is a developer package, not the customer installer.
If `bin/hp` is missing or incompatible, report an incomplete installation to the
operator; do not ask the customer to install Python, Node, Git or a separate CLI.
The package helper `hp_local_editor.py` never invokes Git, installs dependencies,
runs supplied scripts, or makes network requests. The separate `hp_preview.py`
launcher serves a loopback preview and uses the installed Codex app-server for
allowlisted HP tools. Its chat uses the desktop runtime with existing ChatGPT login
to propose bounded local text edits; it never parses or copies credentials. The
editing model has no HP tools. Explicit dev quick replies save the exact candidate
through the existing service before preparing dev.
Remote operations use the authenticated HP service below. Read [the wire contract](references/protocol.md) when
integrating a service or diagnosing rejected packages.

## Customer guide

For onboarding, ZIP installation, or explaining the current UI, read
[the Japanese customer guide](references/customer-guide.md). Guide the customer
through the installed desktop app and existing Google connection. The local
browser chat is the primary editing surface after startup.

## Plugin setup

Preserve the user's complete request through installation and login. For
“導入して、編集を始めたい”, completion means an authenticated site selected and
its local browser editor open. After connection succeeds, continue the already
requested editing start. `doctor` proves runtime availability only.

Check the current version and loaded plugin capabilities first. Continue when
the installed version is available in the host. Request restart only when the new
files remain unloaded and supported reload mechanisms cannot resolve it.
When the host requires a restart or the user's Google login, report the exact
remaining user action and ask them to return to the same conversation with
“続けて”. Continue the original request when they return. If the host opens a new
setup conversation, use the user's current editing-start request there. Do not
present a runtime check as successful Google connection or an opened editor.

For a connection or editing-start request, run the bundled `bin/hp doctor`, then discover the plugin's
HP control tools. The configured endpoint is `https://hp-control.simy.one/mcp`.
Installing this package is not evidence of a working connection. Add only the
requested plugin settings, preserving existing configuration; do not invent an
app registration or claim OAuth setup succeeded. The host must discover
protected-resource/OAuth metadata, complete
Google login in the user's own UI, and verify `hp_profile` plus `hp_list_sites`.
The server selects the validated Google subject and site roles; never supply a
subject or repository as a tool argument.

Use HP’s authenticated `hp_download_workspace` / `hp_prepare_upload` through the
bundled transfer commands for file transport. No customer Drive plugin is needed.
The desktop app’s bundled Codex runtime uses the existing HP connection; do not
install another CLI or copy its credentials. If an actual host cannot reuse the
connection, identify that setup gap rather than declaring it connected. Compare
the authenticated profile used in this chat with the local control’s profile.
The service binds transfers to the caller’s site/workspace and immutable package.
Private transfer URLs stay inside the helper, never chat, logs or public receipts.
If direct transport is unavailable, the operator must enable the compatible
service; do not silently fall back to extra plugins or the old intake skill.

Before claiming support on a Codex host, verify OAuth discovery/DCR, private
result metadata and app-only confirmation there. If a host needs a registered
MCP mapping, use its actual approved ID and keep personal registrations out of
the public package. Draft saving and publication are separate; service gates and
the user's exact-artifact confirmation govern publication.
For install-only requests, verify the installed version in the host and stop at
that scope. For connection-only requests, finish with authenticated profile and
site readback. Preserve an already-requested editing start through either phase.

## Customer experience: local, dev, production

When the customer says “HP編集を始める” or, for example, “診療時間を変更したい”, handle the
preparation yourself. Verify the connected profile and available sites. Use the
named site, or the sole available site; ask the site’s name only if ambiguous.
Reuse the current validated workspace, preserving pending work. If none exists,
request a snapshot and initialize a dedicated folder under the user’s current HP
project (or `~/Documents/HP編集/<site_id>` when no project was designated). Never
overwrite an existing workspace to make it look fresh. The customer need not
choose paths, start a terminal, run commands or know skill/tool names.

Start local preview as part of the editing request. The launcher opens its actual
URL in the default browser automatically. If a live preview already exists, reuse
its validated URL and open it instead of starting a duplicate. An editing-start
request needs no file modification before showing the page.
A bottom-right icon opens the local editing chat. The user can continue there
without switching back to the app. This is an HP-specific conversation whose
history is stored in the workspace, not a writable clone of this desktop thread.
Explain the change in ordinary Japanese. Do not require a separate “start server”
request. Follow these boundaries even if the customer originally asked to publish:

1. **手元で確認**: edit and show the local result; do not change dev or production.
2. After the customer accepts this local version and requests the next stage,
   save the draft, read back and accept the exact receipt, then prepare **確認用サイト
   (dev)**. A human click on the local menu’s dev-preparation button is also that
   stage’s request. The widget saves the immutable local candidate first when
   needed, then prepares dev. Verify completion and
   open the remote URL for the customer to check; local success is not dev proof.
3. After the customer checks that dev version, prepare its publication card/dialog.
   **本番公開** requires the customer’s own exact-artifact confirmation click.
   Local approval or a request to prepare dev does not authorize production.

If files or the publication target change after either check, show the changed
version again. Explain progress using these three stages; keep IDs, CLI commands,
repository details and hashes out of the normal user flow. Keep the technical
receipts for verification and interruption recovery. Never click confirmation on
the customer’s behalf or automatically promote through all three stages.

## Immediate local preview and two confirmation surfaces

For local display or floating controls, read [local preview](references/local-preview.md).
Launch the installed `bin/hp preview` with the existing validated workspace.
The launcher opens the loopback URL automatically; edits under `site/` reload automatically.
If automatic opening fails, open the exact printed URL for the customer.
A successful launch command and a live URL establish server readiness; confirm
browser display with available host evidence. If browser observation is unavailable,
provide the live URL and distinguish the opening request from observed display.
The local shell keeps controls separate from the sandboxed site. Do not inject
publication controls or credentials into site files. Local preview requires no
cloud save or deployment.

The local widget labels are 確認用に反映, 確認用サイトを開く, and 公開する.
Explain 確認用 / 公開中 as content versions, not deployment timestamps. A version
belongs to a verified artifact and is reused when it is published. Display dates
only as supplementary information. The publication dialog shows the source-to-target
version transition and bound change summary. A completed result shows its changes
and public link. Details and summaries never grant publication authority.

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
| Download / upload with the bundled helper | `hp_download_workspace`, `hp_prepare_upload` |
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
helper's `bundle_sha256` and `bundle_file_id` is returned by the bundled `upload`
command for those same pending bytes. A queued/running operation has not saved or published yet.

Keep public control requests and results in a local `control-receipts/` directory
beside the site workspace so a new Codex session can recover the same operation
IDs. Keep these records outside `site/`; do not copy OAuth tokens, signed download
URLs, or private confirmation metadata into them.

For an open-workspace operation, read its `result` only after authenticated
completion and save that exact `kind: "workspace"` object as `open-result.json`.
Download that owned workspace with the bundled `download` command, then validate it against that
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

Commands below are relative to the installed plugin root; Codex resolves the
actual absolute path and executes them. The customer does not run them manually.

Initialize a new dedicated workspace from a service-provided snapshot:

```sh
bin/hp download open-result.json snapshot.zip
bin/hp init snapshot.zip workspace --receipt open-result.json
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
publication. During an authorized editing task, follow the local/dev/production checks above;
save before preparing dev, or when the user explicitly asks to save a draft.
Setup-only, local-only or discussion-only requests do not authorize draft writes. Report saved status only after the exact receipt
has been read back and accepted locally.

```sh
bin/hp status workspace --diff
bin/hp pack workspace changes.zip --summary 'Update opening hours'
bin/hp upload workspace
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
bin/hp accept workspace saved-receipt.json
bin/hp status workspace --diff
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
