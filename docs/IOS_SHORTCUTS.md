# iPad shortcuts — one tap instead of nine

Phase 13 Stage 1. The download path is: Safari → github.com → Actions →
Live slate → Run workflow → wait → the run → Artifacts → zip → Files →
long-press → Uncompress → tap. Nine steps before you see a number.

Two iOS Shortcuts collapse that. **Neither needs any change to this
repository** — they call GitHub's REST API, which is already there.

| Shortcut | Does | Taps |
|---|---|---|
| **Velocity — Run** | dispatches `live-slate.yml` | 1 |
| **Velocity — Status** | tells you the last run's state | 1 |

With email delivery configured ([`EXCEL_IPAD.md`](EXCEL_IPAD.md) §3), the
whole loop becomes: tap **Run**, wait, open the email, tap the attachment.

---

## 1. The token (once)

A **fine-grained personal access token**, scoped to this repository only.

1. github.com → your avatar → **Settings** → **Developer settings** →
   **Personal access tokens** → **Fine-grained tokens** → **Generate new**.
2. **Repository access:** Only select repositories → `EdgeCash/Velocity`.
3. **Permissions → Repository permissions → Actions: Read and write.**
   That is the only one needed. Not `contents`, not `workflows`.
4. Expiry: 90 days is a reasonable default. Put a reminder in your calendar
   — an expired token makes the Shortcut fail with a 401, which reads like
   the repo is broken.
5. Copy the token. It is shown once.

> This token can start workflow runs in this repository and nothing else. It
> cannot push code, read your other repositories, or change the workflows
> themselves. Store it in the Shortcut, not in a note.

---

## 2. Shortcut — "Velocity — Run"

Shortcuts app → **+** → add these actions in order.

**Action 1 — Text** (this is the request body):

```json
{"ref":"main","inputs":{"leagues":"nfl ncaaf","bankroll":"100"}}
```

**Action 2 — Get contents of URL**

| Field | Value |
|---|---|
| URL | `https://api.github.com/repos/EdgeCash/Velocity/actions/workflows/live-slate.yml/dispatches` |
| Method | `POST` |
| Headers | `Authorization` → `Bearer YOUR_TOKEN_HERE` |
| | `Accept` → `application/vnd.github+json` |
| | `X-GitHub-Api-Version` → `2022-11-28` |
| Request Body | **File** → choose the Text from Action 1 |

**Action 3 — Show Notification**: `Velocity run dispatched`

Rename it **Velocity — Run**, give it an icon, then **Add to Home Screen**.

### What to expect

A success returns **HTTP 204 with an empty body** — that is correct, not a
failure. The Shortcut shows your notification and nothing else.

| Response | Meaning |
|---|---|
| 204, empty | dispatched. The run appears in Actions within ~30 seconds |
| 401 | token expired or mistyped |
| 403 | token lacks **Actions: Read and write** |
| 404 | wrong repo or workflow filename — or the token cannot see the repo |
| 422 | `ref` is not a branch that exists, or an input name is wrong |

### Variations worth having

Duplicate the Shortcut and change only the body or the workflow filename:

* **Re-export only** (≈2 minutes, no re-simulation) — same headers, URL ends
  `refresh-exports.yml/dispatches`, body
  `{"ref":"main","inputs":{"leagues":"nfl ncaaf","bankroll":"100"}}`
* **Props now** — URL ends `collect-football-props.yml/dispatches`, body
  `{"ref":"main"}`. Use this when the board comes back DEGRADED on props,
  then run the slate again.
* **Different bankroll** — change `"bankroll"` in the body.

---

## 3. Shortcut — "Velocity — Status"

Answers "did it work?" without opening Actions.

**Action 1 — Get contents of URL**

| Field | Value |
|---|---|
| URL | `https://api.github.com/repos/EdgeCash/Velocity/actions/workflows/live-slate.yml/runs?per_page=1` |
| Method | `GET` |
| Headers | same three as above |

**Action 2 — Get Dictionary Value**: key `workflow_runs` → *Value*
**Action 3 — Get Item from List**: *First Item*
**Action 4 — Get Dictionary Value**: key `status`
**Action 5 — Show Result**

For more than the bare status, add a second **Get Dictionary Value** for
`conclusion` and one for `html_url`, and show all three.

Reading it:

| `status` | `conclusion` | Means |
|---|---|---|
| `in_progress` | — | running; give it 10–15 minutes |
| `completed` | `success` | the board passed the readiness gate |
| `completed` | `failure` | **the gate failed** — a required surface is missing, or the numbers are too stale to replace before kickoff |

A `failure` here is the readiness gate doing its job, not the pipeline
crashing. The artifact and the email are sent *before* the gate runs, so a
failed run still delivered a workbook — open it and read **Run status** on
the Dashboard for the reason.

---

## 4. The game-day loop, with shortcuts

About three hours before the first kickoff:

1. Tap **Velocity — Run**.
2. Do something else for fifteen minutes.
3. Tap **Velocity — Status**. Green → open the email, tap the workbook.
4. Red, or the workbook says DEGRADED on props → tap **Props now**, wait,
   tap **Velocity — Run** again.

Full routine and per-surface fixes: [`GAME_DAY.md`](GAME_DAY.md).

---

## 5. Why this and not an app

The Shortcut is a button on a home screen that calls an API GitHub already
exposes. There is nothing to deploy, nothing to host, no secret sitting on a
server, and nothing that can drift out of step with the pipeline — if the
workflow changes its inputs, the worst case is a 422 telling you so.

Any dashboard worth building later ([`PHASE13_MOBILE.md`](PHASE13_MOBILE.md))
would still want exactly this dispatch call behind its refresh button. This
is that button, without the dashboard.
