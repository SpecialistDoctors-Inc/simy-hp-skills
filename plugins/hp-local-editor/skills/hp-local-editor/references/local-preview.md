# Local preview and confirmation

Use this route when the user wants immediate visual feedback or floating controls.
The package helper and local preview launcher are separate: the former has no
network calls; the latter uses installed Codex to access the already connected HP
service. It starts no model turn and stores no copied OAuth credentials.

## Launch

After the authenticated snapshot has been initialized, use the actual installed
skill path and the existing workspace (not its `site/` subfolder):

```sh
python3 scripts/hp_preview.py /absolute/path/to/workspace --codex /absolute/path/to/codex
```

Read the printed `http://127.0.0.1:<port>` URL and open it in the user's browser.
Record the exact launched PID, PPID, command and start time. Keep the process
running while the user is previewing. To stop an owned launcher, verify the same
identity immediately before a graceful SIGINT to that exact positive PID. Never
stop processes by name/group/tree or stop a shared Codex process. The launcher
closes only its own recorded app-server child.

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
