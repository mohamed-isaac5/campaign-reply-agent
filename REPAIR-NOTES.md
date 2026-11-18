# Repair round: acceptance notes

This is a local repair/review artifact, not a production certification. No real Gmail, paid model, real email send, hosted product or repository publication was used during the repair.

## Verified locally

- 103 backend tests passed in a fresh isolated Python environment installed from the dependency snapshot.
- Production Next.js build and type check passed.
- The committed UI acceptance runner passed with a fresh temporary DB, forced offline settings and no browser runtime errors. It exercised English onboarding, refusal/default-off, explicit rule confirmation, empty editor, visible failed response, successful simulation receipt and all five pages at 320, 390 and 1280 pixels.
- All 15 measured page widths equaled the viewport. Actual screenshots were also inspected; width measurements alone are not a visual review.
- Independent review cleared the original defects and two additional cases in repair candidate 2. Later small defensive changes were self-tested: malformed model output fallback, idle Pub/Sub polling, literal recorded-fact drafting and blank optional environment defaults. Final evidence is attached separately to the delivery.

Test environment: Linux, Node 22.23.5, Python 3.10.12. The Google packages report that Python 3.10 is unsupported; use Python 3.11+ for a new installation. Existing FastAPI/TestClient deprecation warnings do not constitute test failures. Dependency installation and fake-contract tests do not prove live-account behavior.

## Defects and regression coverage

| Area | Repair | Main tests / source |
| --- | --- | --- |
| Refusal inverted into permission | Free text cannot grant sending. Dedicated confirmed boolean, default off; old free-text grants revoked | `test_refusals_never_suggest_grant`, `test_free_text_never_grants_execution`; `engine.set_rules` |
| Paused / low-confidence sends | Active-campaign, kill, suppression and >=85% gates at claim; validated classifier schema | `test_paused_campaign_never_auto_sends`, `test_low_confidence_never_auto_sends`, `test_invalid_model_schema_fails_closed` |
| Decimal/currency loss | Exact positive decimal plus explicit supported currency; full match, no truncation or guessed symbol country | `test_price_is_exact_and_unambiguous`; `engine.extract_price` |
| Mixed price/discount questions | Independent mixed-intent safeguards plus deliberately narrow price-only phrase scope | `test_mixed_intent_never_auto_sends`; `engine.price_only_question` |
| Stop combined with commercial text | Dedicated opt-out signal check before every commercial branch and historical-context sync | `test_optout_is_independent_of_commercial_intent`; `llm.has_optout` |
| Concurrent approval attempts | Serialized pending-to-sending claim, saved exact body and outbound ID before I/O | `test_parallel_approval_has_only_one_outbound_attempt`; `engine.execute_action` |
| Approval after opt-out | Address-wide suppression and pending-draft cancellation; gates rechecked before send | `test_old_draft_cancelled_after_optout_across_threads_and_campaigns` |
| Approval after changed request | Draft anchored to the triggering inbound message; newer customer context blocks approval and is visible in UI | `test_older_draft_is_blocked_after_newer_request_change`, `test_newer_gmail_context_blocks_old_draft_before_send` |
| Empty edited reply restored | `None` means no edit; empty string means empty. API rejects empty approval and UI disables it | `test_empty_edit_is_not_restored`; UI acceptance |
| Inbound lost after crash | Durable inbound state/lease; inbound completion and action committed together; recovery resumes unfinished work | `test_interrupted_classification_resumes_without_lost_action`, `test_hard_crash_inbound_lease_is_recovered` |
| Uncertain outbound duplicated | No automatic resend. Exact RFC Message-ID reconciliation only; absent evidence keeps it unresolved | `test_interruption_after_claim_never_blind_retries`, `test_b3_uncertain_attempt_is_reconciled_and_never_resent`, exact-identity/MIME tests |
| Worker signature mismatch | Explicit parsed metadata contract; ordinary full MIME event runs through actual worker method | `test_worker_contract_fullbody_and_context_before_classifier` |
| Two Gmail threads collapsed | Gmail thread ID is part of local thread identity; changed recipient blocks processing | `test_gmail_thread_identity_not_contact_identity`; `engine.ensure_thread` |
| Snippet / missing context | Full MIME decoding, HTML fallback, timestamps, context sync before classification and pre-send refresh | Multipart/full-body/worker-context tests; `gmail_client.full_body`, `worker._sync_thread_context` |
| Recovery first page only | All pages read, oldest first; checkpoint captured before full listing; recovered historical events review-only | `test_full_sync_reads_all_pages`, `test_expired_recovery_captures_checkpoint_before_listing`, `test_full_sync_recovery_is_review_only_even_when_price_permitted` |
| Idle Pub/Sub looked failed | Idle deadline returns empty notifications; fallback history reconciliation continues | `test_idle_pubsub_deadline_is_empty_not_worker_failure` |
| Silent model failure | Invalid/unavailable real-model classification becomes human review; test-connection reports failure | `test_invalid_real_model_json_goes_to_review` |
| Blank .env optional values | Empty model/base-URL uses defaults; ADC path is an optional commented example, not an empty forced path | `test_blank_optional_environment_values_use_provider_defaults` |
| Misleading metrics | Approved outcomes counted on decision day; DNC distinct addresses; uncertain/current queue separate from daily snapshot | Decision-day, DNC-count and digest tests |
| Mobile overflow / invisible feedback | Responsive bottom navigation and minimum-width fixes; errors, receipts, busy guards and disabled empty/stale approval | `frontend/tests/ui-acceptance.mjs` and actual pixel inspection |

## Experience changes

- Effective rule, exact price, confidence threshold and always-review cases are visible before enabling automatic replies and in Settings/campaign details.
- Review includes full local context, fact provenance, exact editable reply, specific reason, saved receipt and decision history. Campaign details route pending actions to that full review instead of bypassing the editor.
- Rule-based drafts can quote literal relevant supplied facts. Optional real-model drafts use context/facts/tone but remain unverified human-review suggestions, never automatic answers.
- Worker/local-processing health and unresolved outbound attempts are visible. A stale heartbeat is not shown as currently healthy.
- Follow-up requests are actionable manual tasks, with explicit wording that no scheduling or sending happened here.
- Fictional sample campaign results are separated from default Campaigns totals.

## Remaining boundaries

- No live Gmail/model end-to-end verification or delivery proof. Gmail acceptance is not recipient delivery/read confirmation.
- Offline keyword matching is conservative and incomplete, including possible false-positive opt-outs. Unrecognized price phrasing goes to review. Human review is still needed.
- A switch/opt-out cannot recall an already in-flight request.
- The local app has no multi-user authentication, encryption-at-rest service or production deployment controls. Keep it on loopback and protect the host/token files.
- One worker configuration maps one label to one explicit campaign ID. No inferred ownership across arbitrary campaigns or accounts.
- The do-not-contact list does not control another campaign sender. Stop that sender separately.
- An uncertain legacy send without durable identity may remain unresolved indefinitely and needs manual investigation.
- `requirements-lock.txt` and the npm lockfile aid reproducibility; they are not a supply-chain audit. Review updates before adopting them.

## Additional independent final-review round

Addressed all 11 supplied cases: invalid-provider fail-closed validation and visible status; current authored text separated from quoted consent history including HTML-only MIME; onboarding reload restoration; readable phone action stacking; semantic keyboard expanders; global distinct DNC aggregate respecting sample selection; MCP unresolved queue parity; atomic campaign name/goal/plan edits; all advertised history rows displayed; explicit campaign missing/load-failure states; MCP thread actions included. Added backend regression cases in `test_final_review.py` and expanded UI acceptance. Existing fake drafting contract now names a supported provider while still replacing every network call with a local fake. No live integration verification or production-readiness claim is added.

### Candidate 4 consent regressions

- Outlook `divRplyFwdMsg` headers start a persistent historical section in direct HTML and readable MIME text, including sibling campaign/footer blocks after the header closes. Full history is still retained for review. Mozilla cite headers were initially included in that latch, but candidate 5 corrects this: they are container-scoped so new bottom/inline paragraphs remain authored.
- Removed whole-line footer filtering. Only a narrow instructional phrase can be excluded from stop matching; the remainder of that same line is always checked, so a genuine current stop following a footer-like prefix still suppresses the address. Unsupported or uncertain phrasing remains a conservative review/manual-verification limitation.
- Added direct HTML, HTML-only MIME, worker-context and six same-line stop-prefix regression cases. No additional frontend changes.

### Candidate 5 consent and first-run recovery

- Mozilla cite-prefix headers are container-scoped, not a permanent history latch. Current authored text before, after or between closed blockquotes remains available for stop matching. Outlook header-only history layout remains a distinct persistent boundary.
- Regression tests cover Thunderbird-style bottom and inline stops through raw HTML and full MIME, followed by an exact price request in another campaign from the suppressed address. The exact independent reviewer fixtures are also rerun separately.
- Chat initialization catches status/onboarding read failures, shows a useful Retry button, and keeps inputs disabled until the correct saved question loads. Retry refetches without a page refresh, restores the question and input, and clears the stale error banner. Browser acceptance injects that failure and verifies recovery with no unhandled rejection.

### Candidate 6 wrapped plaintext attribution

- A conventional English `On ... wrote:` attribution may span up to six contiguous physical lines. The splitter recognizes that bounded header before scanning the historical body/footer. A blank line or quoted line ends the header candidate; current text before it is kept exactly.
- Regression coverage compares single-line, two-line and four-line attributions with both prior STOP and click-to-unsubscribe footers. Current English/Arabic multiline stops before the header still suppress, and ordinary multiline prose with a blank separation is not consumed as an attribution.
- Exact independent wrapped-header fixtures are rerun separately. Earlier raw-HTML/MIME, Outlook/Mozilla, bottom/inline stop, cross-campaign suppression and onboarding retry tests remain included. This adds supported conventional formats, not a claim of universal email parsing.

### Candidate 7 bounded plaintext bottom-reply fix

- Single/wrapped attribution handling now distinguishes explicit quoted line blocks from unmarked historical bodies. When the first historical content is `>`-quoted, those quoted lines are skipped and later unquoted bottom/inline paragraphs remain authored. Unmarked prior campaign bodies after attribution still terminate authored-text extraction.
- Added English/Arabic bottom-stop, other-campaign later-price blocking, quoted old-stop plus positive bottom reply and inline-between-quotes regressions. All earlier cases remain included. No feature/polish changes or new automatic permissions.
- Local review artifact is frozen after the bounded independent retest. Real account/provider behavior and arbitrary email parsing remain unverified; source sender handoff and controlled E2E require separate work.
