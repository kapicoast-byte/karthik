# Slack → Plane Catalog Task Automation — Scope & Analysis

Source: client document *"Slack → Plane Catalog Task Automation — Workflow
Specification, Setup Steps & Expected Results"* (12 sections).
Status: **pre-contract analysis.** Nothing here is agreed with the client yet.

---

## 1. What the client is asking for

One sentence: *an always-on assistant that watches their Slack, decides what is
real work, and keeps Plane as the single source of truth — with deadline and
follow-up chasing on top.*

Five capabilities:

| # | Capability | Doc §  |
|---|------------|--------|
| 1 | Selective capture — only actionable messages become tasks (DMs, mentions, thread replies) | 3, 10 |
| 2 | Structured classification into a fixed 13-field template | 4 |
| 3 | Plane write-through with duplicate suppression | 5 |
| 4 | Time-based nudges: due dates, overdue, 2-day "waiting on team" chase | 6, 7 |
| 5 | Daily digest ("control tower") incl. open Amazon cases | 9 |

The non-functional theme running through §10 is **precision over recall**. The
client has been burned by noise: "don't invent", "don't create a task just
because I was mentioned", "ask when ambiguous". A system that files 40 junk
tasks a day fails even if every other feature works. Design accordingly —
when in doubt, ask, don't file.

## 2. The delivery-model fork

§11 Step 3 says "connect Plane Cloud through an available supported
integration/API/**MCP** route" and §12 says "**ChatGPT** identifies and
classifies the work". That wording suggests the client may be picturing
*ChatGPT itself with connectors* — a no-code setup — rather than a service.

| | Option A — chat assistant + MCP connectors | Option B — hosted service |
|---|---|---|
| Effort | Days | ~5–6 weeks |
| Runs when | A human opens the chat | Continuously |
| §6 reminders | ✗ | ✓ |
| §7 two-day follow-ups | ✗ | ✓ |
| §9 daily 8am digest | ✗ | ✓ |
| Missed messages between sessions | Yes | No |
| Coverage of this document | ~40% | ~100% of what is technically possible |

**Recommendation: Option B.** Option A is worth offering only as a 1-week pilot
to prove classification quality before committing to B.

This repository implements **Option B**.

## 3. Architecture

```
Slack Events API ──▶ intake queue ──▶ LLM classifier (structured JSON)
                                            │
                              ┌─────────────┴──────────────┐
                        ambiguous?                    confident?
                              │                             │
                    Slack DM w/ buttons            dedupe check ──▶ Plane REST API
                    (Confirm / Edit / Ignore)            │
                              └──────────────▶ state DB (SQLite → Postgres)
                                                          ▲
Plane webhooks ───────────────────────────────────────────┤  (status sync back)
Scheduler (APScheduler) ──────────────────────────────────┘
   └─▶ due-soon / overdue pings · 2-day follow-ups · daily digest → Slack DM
```

The **state DB** is the piece the client did not ask for but the spec cannot
work without. It is what makes possible:

- §5 duplicate detection (Slack message ↔ Plane work item mapping)
- §6 "stop reminders once completed" (what we already notified about)
- §7 two-day timers (when we last heard from the assignee)
- §8 thread monitoring (which thread messages we have already folded in)

## 4. Requirement-by-requirement disposition

| Doc § | Requirement | Verdict | Note |
|---|---|---|---|
| 3 | Monitor DMs, mentions, thread replies | **Conditional** | DMs need a Slack *user* token + admin approval — see Q1 |
| 3 | Ignore chatter, FYIs, acks | Build | Classifier gate; tune on real history |
| 3 | Flag ambiguous rather than guess | Build | Slack DM w/ Confirm / Edit / Ignore buttons |
| 4 | 13-field task template | Build | `src/catalogbot/models.py` |
| 5 | One Plane task per work item | Build | Plane REST API |
| 5 | No duplicates | Build | Exact key + fuzzy/semantic match against open items |
| 6 | Use stated due date only | Build | |
| 6 | Don't invent a date for "ASAP" | Build, **with a gap** | See Q4 |
| 6 | Upcoming / due-today / overdue reminders | Build | Own scheduler; Plane's native notifications are too weak |
| 6 | Stop reminders when done/cancelled | Build | |
| 7 | 2-day follow-up when waiting on team | Build | |
| 8 | Thread monitoring, latest instruction wins | Build | Re-classify on thread reply, patch the Plane item |
| 9 | Daily digest of overdue / due-soon / waiting | Build | |
| 9 | **Check open Amazon cases every morning** | **Blocked** | No public API for the Seller Central case log — see Q2 |
| 10 | Guardrails (no invented IDs/dates, no auto-complete) | Build | Enforced in prompt *and* in code |

## 5. Open questions — must be answered before quoting

Tracked in `docs/open-questions.md`. The two that change the price are Q1
(Slack DM access) and Q2 (what "check Amazon cases" actually means).

## 6. Phasing

| Phase | Scope | Est. |
|---|---|---|
| 0 | Access: Slack app approval, Plane API key, project/state mapping | 3–5 d (mostly client-side) |
| 1 | Capture + classify + Slack confirm loop + create in Plane | 1.5–2 wk |
| 2 | Dedupe + thread monitoring + Plane→Slack status sync | 1 wk |
| 3 | Due-date reminders + 2-day follow-up engine | 1 wk |
| 4 | Daily control tower digest | 3–5 d |
| 5 | Amazon cases | TBD, depends on Q2 |

Phase 1 alone removes the manual copying, which is the client's loudest pain.
Position it as the first billable milestone.

## 7. Acceptance criteria worth agreeing up front

Because §10 is really a quality bar, propose measuring it:

- **Precision** ≥ 0.9 on a labelled sample of ~200 real Slack messages
  (of the tasks we file, ≥90% are genuinely tasks).
- **Recall** ≥ 0.8 (of the real tasks, we catch ≥80%; the rest surface as
  "ambiguous" prompts rather than silence).
- **Zero fabricated** ASINs / SKUs / dates in a 200-message sample — this is a
  hard-fail criterion, not a percentage.
- **Zero duplicate** Plane items across the sample.

Build the labelled sample during Phase 0 from a Slack history export; it is
also the eval set used to tune the classifier.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Slack admin refuses the user-token install | Confirm in Phase 0, before any build work. Fall back to channels + mentions only. |
| Classifier files noise, client loses trust in week 1 | Ship Phase 1 in *confirm-everything* mode; relax to auto-file per category once precision is measured. |
| Amazon case check turns out to mean Seller Central scraping | Scope out; offer the Plane-tracked-cases version instead. |
| Slack message content leaving the workspace to an LLM | Explicit client sign-off + channel allowlist + no message retention beyond the task record. |
| Plane API rate limits / schema drift | Single client wrapper (`plane.py`), retries, contract test against a sandbox project. |

## 9. Verified against a live Plane workspace

Run `python scripts/verify_plane.py` to reproduce. Confirmed on a Plane Cloud
Business trial (2026-09-09):

- Base URL `https://api.plane.so`, auth header `X-API-Key`.
- Full CRUD works: read states and labels, create, update, list, delete.
- Default state set is **Backlog, Todo, In Progress, Done, Cancelled**.
  There is **no Blocked state** — the mapping in `plane.py` was corrected.
- **Outbound webhooks are available**, so Phase 2 can use push rather than
  polling — on this plan. The client's plan still needs confirming (Q3).
- Personal access tokens (`plane_api_...`, 42 chars) work for the full API;
  a workspace-level token was not required.

**Trap worth knowing:** Plane matches the `X-API-Key` header name
case-sensitively. Any client that normalises header names — `urllib`
title-cases it to `X-Api-Key` — gets `403 "Given API token is not valid"`
with error code 1010, for a perfectly valid token. The error points at the
credential, so it costs hours if you do not know. `httpx` and PowerShell send
it verbatim and are fine.
