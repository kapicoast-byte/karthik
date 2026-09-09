# Open questions for the client

Ordered by how much they change cost and scope. Q1 and Q2 gate the quote.

---

### Q1 — Can we read your Slack DMs? *(blocks §3)*

A Slack **bot** can only see channels it is invited to. It cannot see your
direct messages. §3 explicitly requires DMs, so we need a Slack **user token**
installed by you, with scopes `im:history`, `mpim:history`, `channels:history`,
`groups:history` — and that usually needs **workspace-admin approval**.

- **(a)** Admin will approve a user-token app → full §3 as written.
- **(b)** Admin will not → scope shrinks to channels the bot is invited to,
  plus `@mentions`. DM-only requests would be forwarded manually.

**The technical half is settled.** We built the Slack app and installed it on
our own workspace: the user token is issued, the DM-reading scopes are granted,
and capture works. So this is now purely a question of whether your admin
approves the install — not whether it can be done.

*We still need that answer before we can quote Phase 1.*

---

### Q2 — What does "check open Amazon cases every morning" mean? *(§9)*

The Seller Central case log has no public SP-API endpoint, so this is not
automatable the way the sentence implies.

- **(a)** The digest reports Amazon-case *tasks already tracked in Plane*
  (recommended, no extra integration).
- **(b)** Someone forwards/pastes case updates into a Slack channel and we
  parse those.
- **(c)** Browser automation against Seller Central — brittle, breaks on UI
  changes and MFA, and may conflict with Amazon's terms. **We advise against it.**

---

### Q3 — Plane structure and status mapping *(§4, §5)*

- Which Plane **workspace** and **project(s)**? One project, or one per
  marketplace?
- Your 7 statuses must map onto Plane states. Plane's default set, confirmed
  against a live project, is Backlog, Todo, In Progress, Done, Cancelled —
  there is no Blocked state. So we propose:

  | Your status | Plane state (group) | Label |
  |---|---|---|
  | New | Todo (*unstarted*) | — |
  | In Progress | In Progress (*started*) | — |
  | Waiting on Me | In Progress (*started*) | `waiting:me` |
  | Waiting on Team | In Progress (*started*) | `waiting:team` |
  | Waiting on Marketplace | In Progress (*started*) | `waiting:marketplace` |
  | Completed | Done (*completed*) | — |
  | Cancelled | Cancelled (*cancelled*) | — |

  Confirm this, or tell us you would rather add a custom **Blocked** state (or
  three separate Waiting states) to your board — either is fine, it is one
  line of configuration, but it has to match what your reporting expects.
- Where do Marketplace (Amazon/Walmart/Other) and Priority live — Plane's
  native priority field + labels, or custom properties?
- Does your Plane plan expose **API tokens** and **outbound webhooks**? We need
  both (webhooks are how status changes made in Plane flow back to us).
  On a Plane Cloud Business trial we confirmed both are available, so the
  integration is proven — but we have not confirmed what the Free tier keeps.
  Please tell us **which plan you are on**, and whether **Webhooks** appears
  under your Workspace settings. If it does not, Phase 2 has to poll Plane on
  a timer instead of receiving pushes: more code, slower to notice changes,
  and priced separately.

---

### Q4 — What should "ASAP" mean? *(§6)*

§6 correctly forbids inventing dates. But a task with no due date never enters
the reminder system, so "ASAP" work silently rots — the exact failure §1 wants
to prevent.

Proposal: `ASAP` → **next business day**, `this week` → **Friday**, flagged in
the task as an *assumed* date so it is visibly a default, not a fact. Confirm
or give us your own rule.

---

### Q5 — Who is covered, and who confirms?

- One person's Slack, or the whole catalog-ops team? (Changes cost and design.)
- Where should the bot ask its clarifying questions — a DM to you, or a private
  `#task-inbox` channel the team can see?
- Should the confirmation prompt time out? Proposal: no reply in 24h → the
  draft is dropped and listed in the next daily digest, never silently filed.

---

### Q6 — Completion *(§10)*

§10 says never auto-complete. Confirm that closing a Plane item is always a
human action, and that a Slack "done!" only ever *suggests* completion (we post
a "mark complete?" prompt rather than closing it).

---

### Q7 — Privacy sign-off

Slack message text is sent to an LLM for classification. We need explicit
written sign-off, and we recommend a channel allowlist so only relevant
channels are ever read. Any channels that must be excluded outright?

---

### Q8 — Hosting

Where does this run — your cloud account or ours? A small always-on container
plus a database. Affects who holds the Slack and Plane credentials.
