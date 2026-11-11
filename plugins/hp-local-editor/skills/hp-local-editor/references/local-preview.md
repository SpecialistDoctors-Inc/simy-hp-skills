# Local preview and confirmation

Use this route when the user wants immediate visual feedback or floating controls.
The bottom-right launcher opens a chat with contextual quick replies: 確認用に反映,
確認用サイトを開く and 公開する. 公開する only opens the final confirmation dialog;
it does not itself publish. Chat requests run local Codex with the existing desktop
ChatGPT login and a bounded structured-edit response. Requests and history persist
under .hp/chat; the model cannot call HP tools or publish.
Connection and original-operation readback run automatically. No manual status button is required.
Readback retries never create a new preview, publication, or notification.
The package helper and local preview launcher are separate. The helper has no
network calls. The launcher accesses the existing HP connection for explicit
save/dev/publication controls, and starts local editing model turns only after
the user sends a chat message. No OAuth credentials are copied.

## Launch

After the authenticated snapshot has been initialized, use the actual installed
skill path and the existing workspace (not its `site/` subfolder):

```sh
bin/hp preview /absolute/path/to/workspace
```

Resolve `bin/hp` relative to the installed plugin root, not the customer workspace.
The native runtime includes Python and discovers the desktop app’s bundled Codex;
no separate CLI or interpreter installation is a customer step. Run `bin/hp doctor`
for diagnosis. A missing bundled runtime is an operator packaging/setup problem.
The `--codex` override is a developer diagnostic option, not normal onboarding.

The launcher opens its `http://127.0.0.1:<port>` URL in the default browser after
both local servers are ready. If opening fails, it keeps serving and prints the
URL so Codex can open it for the user. Use `--no-open` for automated checks only.
For an existing live launcher, reopen its verified URL instead of launching again.
Record the exact launched PID, PPID, command and start time at launch. Keep the process
running while the user is previewing. To stop an owned launcher, verify the same
identity immediately before a graceful SIGINT to that exact positive PID. Never
stop processes by name/group/tree or stop a shared Codex process. The launcher
closes only its own recorded app-server child.

The launcher records its URL and process identity in `.hp/control/launcher.json`.
If the workspace lock is already held, read that record and verify the loopback
URL is live before reusing it. The record alone does not grant permission to stop
a process started by another session. Never remove a busy lock to force a restart.

The server binds loopback only and uses separate control and site origins.
Files reload when saved; it does not upload or publish each edit. Local display
still works while the HP service is unavailable. The menu can be collapsed.
Only site-relative assets/navigation are supported in this version: root-absolute
URLs such as `/assets/logo.png`, external APIs, frames and form submission may be
blocked. Report that limitation rather than weakening the sandbox or proxying
arbitrary URLs. The hosted verification remains a separate acceptance step.

## Save, prepare, confirm

- Codex edits and saves through the existing bounded ZIP/Drive workflow. The menu
  does not invent a second save transport. Dirty files or pending packages block
  preparing/publication; preserve them and complete the normal save/accept step.
- Ask the customer to inspect local changes first. A click on “確認用に反映”
  advances to dev only; it never authorizes production. For conversation requests,
  obtain the same local-version acceptance before `hp_submit_preview`.
- The menu requests one stable preview operation for the accepted saved commit.
  A completed `no_changes` result can reuse the current verified preview only when
  the authenticated result includes its exact preview ID, source and artifact.
- The dialog identifies the site and opens the remote verified preview in a
  separate tab. It may include reconciled remote edits; local rendering alone is
  not the publication target. Never weaken remote framing headers to embed it.
- Only an explicit human confirmation uses the private server nonce. It remains
  in launcher memory, never `site/`, public journal, logs or model context. A
  disabled publication gate yields a connection test, clearly labelled no-publication.
- The service binds the same source/artifact for both conversation and local
  surfaces and deduplicates the actual publication. Display the real operation
  status; accepted/queued is not published.

## Resume after interruption

The launcher preserves public request IDs and results in `.hp/control/`. Keep the
workspace and this journal intact when restarting. It never reads credentials out
of Codex files. A changed account or workspace binding blocks remote actions.

For an unreceived preview, retry only the exact original request and operation ID
through the menu's explicit retry action. This relies on server idempotence; do not
create a replacement ID. Confirmation recovery uses `hp_get_confirmation` with
its original approval ID. A confirmed operation is observed, not resent.

If an unaccepted confirmation must be abandoned, use the explicit withdrawal
button. `hp_cancel_confirmation` competes atomically with acceptance. If acceptance
won, the same existing operation is shown and is not rolled back. If withdrawal's
response is lost, read it back or explicitly repeat that same withdrawal; never
repeat the publication confirmation. An expired timestamp alone is insufficient
evidence that a pending confirmation was not accepted.

A previous result can be opened without preparing a new card. A definitive
confirmation refusal permits a new card after its cause is resolved; a successful
confirmation followed by failed readback remains uncertain. Reporting problems
never justify a second publication or mail send.


## Widget chat

The chat retains its own HP-specific history; it does not attach to an arbitrary
active desktop thread. Existing UTF-8 HTML/CSS/JSON/TXT/SVG content up to 220KB
is supported. Images, new files and oversized sites require the normal Codex flow.
The existing desktop ChatGPT login is required; API key setup is not part of this
feature. A turn proposes exact unique text replacements. The host rejects unsafe
paths, mismatched text, and edits that conflict with newer local content.

A pending chat request keeps its ID on retry. Interrupted model responses do not
restart automatically. Persisted candidates can recover without another model
call, only when each affected file still matches the expected before/after bytes.
The dev quick reply persists an immutable save request, accepts the matching
receipt, and then uses the existing dev operation. Pending delivery is not success.
Publication remains a separate exact-artifact confirmation action.


## Revising an earlier change

The user can continue in chat with an instruction such as 「さっきの修正を戻して」.
Use the conversation and current files, and ask which change they mean when the
request is ambiguous. The resulting local change follows the usual dev and
publication checks.

Version 0.3.8 undo records remain readable. An interrupted inverse still offers
中断した変更を再開 so an existing partial application can settle safely.


## Content versions and publication summaries

The widget shows 確認用 vN / 公開中 vN from authenticated site metadata. The
per-site number belongs to an immutable verified artifact. Publication retains
that number, and deployment timestamps are available on hover. Never infer content
ordering from the later publication time or allocate a version in the client.

Publication details come from the exact approval binding. They include from/to
versions and up to five before/after change items plus a remaining count. The
service computes a production-source-to-verified-candidate diff. It freezes the
summary with the approval and omits it if the baseline is mismatched or unavailable.
Long text is excerpted around the change. Large files and non-text changes may
receive a category-level description. Render source excerpts as text only.

A completed publication displays 公開済みの内容 and 公開サイトを開く. A pending
or uncertain publication continues reading its original operation. No publication
button appears for an already completed result. The normal completed-undo notice
is hidden; interrupted legacy undo recovery remains available.
