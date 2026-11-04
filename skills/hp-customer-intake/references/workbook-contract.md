# Homepage management spreadsheet workflow

This is a portable contract for two Codex roles. The customer-side role
interviews the website owner; the developer-side role prepares a dev preview.
They use the same per-customer workbook but separate OAuth identities. The
workbook is the human exchange and audit record. It does not grant repository,
AWS, merge, publish, or email authority.

## Workbook layout

The prepared Google Sheets template has three tabs: `ご利用ガイド`, `Requests`,
and `Events`. In the two data tabs the header is row 4 and data begins at row 5;
rows 1–3 are presentation, not records. Discover and validate the header names
on every connection rather than assuming row 1 or relying only on column letters.
The template starts with no operational request or event rows. The guide's
examples are not runnable work. A native Sheet is not itself a running service;
the trusted runner and notification integration still need to be connected.

`Requests` has one immutable row per `request_id` and `revision`; a read-only
derived view identifies the latest revision for each request. Do not overwrite a
previous proposal to make a new revision:

| Field | Meaning |
| --- | --- |
| `request_id` | UUID generated at intake; immutable across revisions. |
| `revision` | Positive integer; incremented for each changed proposal. |
| `site_id` | Authoritative managed-site ID, resolved from private developer configuration. |
| `outcome` | Requested customer-visible result. |
| `target` | URL/path and exact content or bounded change description. |
| `effective_at` / `expires_at` / `timezone` | Required when timing applies. |
| `assets_rights` | Asset source and requester’s stated usage basis, when applicable. |
| `dependencies` | Reservation, form, analytics, payment, and integration effects when applicable. |
| `acceptance` | Checkable acceptance criteria. |
| `requester_ref` | Verified requester reference, not a free-text claim. |
| `notification_recipient_ref` | Configured recipient reference, never an unverified address. |
| `proposal_state` | Human-readable state only. It is not a lock or approval. |

`Events` is append-only and has at least:

| Field | Meaning |
| --- | --- |
| `event_id` | UUID; never reused. |
| `request_id`, `revision` | Links to the proposal version. |
| `occurred_at` | UTC ISO-8601 timestamp. |
| `actor` | Role and service identity, not an authorization claim. |
| `kind` | `question`, `answer`, `needs_input`, `customer_confirmed_request`, `ready_for_dev`, `claimed`, `dev_preview_ready`, `test_result`, `customer_preview_approved`, `customer_preview_revision_requested`, `approved_sha_recorded`, `published`, or `failed`. |
| `body` | Plain explanation and evidence URLs/IDs. |
| `reply_to_event_id` | Required for answers and follow-ups. |
| `event_idempotency_key` | Stable runner-issued key for deduplicated writes. |

`dev_preview_ready` and either customer preview-decision event include the
exact source SHA and artifact hash. The runner validates the human’s authority
receipt and those immutable values before it may create `approved_sha_recorded`.
The approval record retains the approved dev source SHA, artifact hash, request
revision and immutable build artifact ID. A production workflow on main promotes
those same static bytes without rebuilding them. It records the approved dev
source SHA separately from the main release-workflow SHA. Reject a different
request revision, altered static bytes, a mismatched/expired artifact or source
provenance from another repository, workflow, branch or failed run. The initial
manual path uses a protected GitHub Environment reviewer to verify the customer's
actual decision; typed Sheet fields alone never authorize publication. The standalone runner records the actual human confirmation in its local ledger.
SIMY is optional and is not required for intake, development or approval.
Request revisions and events are append-only. A user or Codex must not edit a
different actor’s event or overwrite shared state. The runner’s durable store
owns claims, retries, authorized transitions, exact-SHA approval bindings, and
email idempotency; it mirrors outcomes into `Events`.

## Wake-up and authorization

If the workbook, binding, or requester authority is not known, do not write to
it, including for `needs_input`; ask the user in chat. API/MCP writes to a
spreadsheet often do not run an `onEdit` trigger. Intake
must explicitly call a trusted wake endpoint after an append, or a reconciler
must poll new event IDs and deduplicate them. The implementation needs one of
those mechanisms before this becomes automated.

The private developer configuration, rather than a sheet cell, maps `site_id` to the
allowed repository, dev/main branches, preview/production domains, AWS account,
notification recipient, and permitted execution identities. It verifies each
binding before work begins. Cell protection and a string such as `approved` do
not prove either identity or authority.

## Email notification contract

Developer-side Codex may send through its configured Gmail connector after
reserving an outbox key, rechecking the live request/lease, and admitting that key
immediately before sending (`outbox-admit` must return `may_send: true`). Check
explicit user authority and resolve a verified recipient
from site configuration and verifying the triggering event's evidence. When
that integration or authorization is absent, prepare a draft only and report
delivery as pending. A draft still requires a resolved recipient. Every request
and draft includes
`request_id`, `revision`, `notification_kind`, `recipient_ref`, and a
deduplication key. The only allowed notification kinds are:

| Kind | Required content |
| --- | --- |
| `needs_input` | Missing field, precise question, request ID/revision, and reply path. |
| `preview_ready` | Request ID/revision, verified dev preview URL, exact tested SHA, test result summary, and review action. |
| `published` | Request ID/revision, verified production URL, exact deployed SHA, production verification result, and a link to the recorded evidence. |

The runner owns recipient authority and idempotency; it reconciles delivery
receipts and suppression with the provider. Provider acceptance is not recipient
delivery. Reservation alone never authorizes a send. The admitted send blocks
configuration changes, newer revisions and cancellation until provider readback;
a cancellation received in that interval is pending, not complete. Reconcile an
uncertain send by stable key without resending. The ledger records the admitted
effect even after lease expiry; it does not provide a transaction across Gmail
and SQLite. The customer intake role does not send developer notifications.

## Forward validation cases

These cases are plain expected behavior for a future runner; they perform no
external write or send.

| Scenario | Expected intake behavior | Expected developer behavior |
| --- | --- | --- |
| Correct `診療時問` to `診療時間` typo on `/access/` | Ask for the exact old/new text and target; do not request assets or reservation information. | Apply only the text correction, test the page, and return a dev preview with SHA. |
| Add a holiday notice from 2026-12-29 to 2027-01-03 JST | Require both dates, JST, target placement, expiry/removal behavior, and acceptance. | Verify scheduled/removal behavior and record the preview evidence. |
| Replace the online reservation link | Require the exact destination and whether the current form/analytics integration changes. | Verify the configured dependency before changing the link; request clarification if it differs. |
| A sheet cell says `approved` after a preview | Record it as untrusted information only. | Do not merge, publish, or email; wait for ledger-recorded exact-SHA approval. |
| Human accepts preview SHA `abc` and artifact hash `def` | Append a candidate `customer_preview_approved` event naming that revision/SHA/hash. | Validate the authority receipt and exact immutable values before recording the approved SHA. |
| API appends `ready_for_dev` | Append succeeds. | Wake the trusted runner explicitly or let the reconciler discover the event; do not rely on `onEdit`. |

## Standalone runtime and future integration

The current implementation target uses developer-side private configuration,
SQLite claims/approval/outbox records, Codex Google Drive/Gmail connectors, and
GitHub Actions with AWS OIDC. A running Codex automation discovers new
`ready_for_dev` events; API writes must not be assumed to fire `onEdit`.
A later SIMY adapter may replace intake discovery, state storage or notification
transport without changing the workbook keys or requiring today’s customer to
register with SIMY. Local tests do not prove live automation or email delivery;
record those separately when the actual path has run.
