# Print agent update: the dashboard now sends PDFs

The dashboard side is live. It already reaches your agent through the tunnel
with the Cloudflare service token and a valid signature. Your `GET /status`
answered with version 1.0.0, printer reachable, idle, `toner-low`, page count
7165.

The dashboard now draws each order sheet itself and sends a finished **PDF**.
The agent no longer receives HTML and must not render anything.

Until your `/status` says it accepts PDFs (change 1), the dashboard sends
nothing to the agent and keeps emailing the printer. So none of the changes
below take effect until you deploy them, and nothing breaks in between.

Nothing else changes: the signing scheme, the Cloudflare hostname and token,
the tunnel route, the duplicate rules, the restart rule, the page-counter
check and the outcome meanings all stay as they are.

---

## 1. `GET /status`: add `formats`

Add a top-level `"formats": ["pdf"]` to the status answer, **only in the
version that handles PDFs as below**:

```json
{
  "agent": "downtown",
  "version": "1.1.0",
  "formats": ["pdf"],
  "printer": {"reachable": true, "state": "idle", "reasons": ["toner-low"], "page_count": 7165},
  "queue": 0
}
```

This is the switch. The dashboard checks it (cached 30 seconds) before
every job.

## 2. `POST /print`: the body carries a PDF

```json
{"id": 812, "order": "T-99460", "kind": "auto|reprint|test", "pdf": "<base64 of the PDF file>"}
```

There is no `html` field any more.

- Base64-decode `pdf`. If it is missing, does not decode, does not start
  with `%PDF-`, or is over 5 MB, answer **400** with
  `{"accepted": false, "reason": "bad-request"}` and print nothing. The
  dashboard reads a 400 as "certainly not printed" and emails that job
  instead.
- Submit the PDF to the CUPS queue as it is (`application/pdf`). The
  driverless queue converts it for the printer.
- **Remove the HTML-to-PDF step** (WeasyPrint or whatever was used) and its
  dependencies. There is no HTML path any more.
- The sheet is Letter size. Long orders are several pages: for a job of
  *n* pages, `printed` means the page counter rose by at least 1, and
  report the actual before and after counts.
- Everything else in the `/print` answers is unchanged:
  - `202 {"accepted": true}`
  - `200 {"accepted": true, "duplicate": true, "outcome": ...}` for an id
    already received, without printing again
  - `503 {"accepted": false, "reason": "<IPP reason>"}` when the printer
    cannot print. Use the printer's own IPP reason names: `media-empty`,
    `media-jam`, `door-open`, `offline`, `toner-empty`, or our
    `unreachable`. An `-error` / `-warning` / `-report` suffix is fine.
- **Same id again means the same job.** The dashboard re-sends a job under
  the same id when it got no clear answer the first time, for example a
  timeout. The duplicate check must catch that: answer with what happened
  to the job, and never print it twice.

## 3. Results go to a new address

Send each job's result to:

```
POST https://dashboard.nmteaco.com/tools/webhook/printAgent.php
```

not `/tools/orders/curbsideAgent.php`.

- Sign the path exactly as `/tools/webhook/printAgent.php`.
- The body is as before:

  ```json
  {"action": "result", "agent": "downtown", "id": 812, "outcome": "printed|failed|uncertain",
   "pages_before": 7165, "pages_after": 7166, "error": "media-jam"}
  ```
- Send the header `User-Agent: nmt-print-agent/<version>`.
- Answers:
  - `200 {"ok": true}`: recorded. A repeat of the same result also gets
    this.
  - `200 {"ok": true, "unknown": true}`: the dashboard never sent that id.
    Stop retrying it.
  - `401`: signature or clock problem.
  - `400`: malformed body.
- On a network error or a 5xx, keep retrying with backoff, as before.

## 4. Test it

1. Deploy the new version. Confirm `GET /status` shows `"formats": ["pdf"]`.
2. Tell the owner. The dashboard then sends a **test page** (no customer
   data) through the full path.
3. Check that one sheet came out, and that you reported `printed` with
   the page counter up by 1.
4. Then run the Phase 4 tests from the original instructions with test
   pages. Use the PDF of whichever test page you received.

## 5. Report back

- The new version number.
- The test-page result: your log line for it (id, outcome, page delta).
- Confirmation that the HTML renderer and its dependencies are gone.
