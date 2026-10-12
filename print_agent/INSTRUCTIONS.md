# Downtown pickup printer: local print agent on Home Assistant

You have control of the New Mexico Tea Co. network (UniFi) and its Home
Assistant box. Your job is to let the dashboard **push** each new pickup
order to the Home Assistant box, which prints it directly on the downtown
printer and reports back whether paper actually came out.

Work in the phases below, in order. **Phase 1 changes nothing. Stop after it
and send the report.**

---

## Background

- The shop takes online orders for pickup at the downtown store. Each one has
  to print on the downtown printer, a small HP laser with HP ePrint. Staff
  describe a Resume button and Ready/Attention lights.
- Today the dashboard (`https://dashboard.nmteaco.com`, hosted off-site)
  emails each order to the printer's HP ePrint address. HP's cloud then sends
  it to the printer. This breaks when the ePrint address changes (HP gives
  the printer a new one whenever Web Services is re-enabled). It also cannot
  tell whether paper came out, so a jam or empty tray goes unseen.
- The dashboard keeps a queue of print jobs, one row per print, with states
  pending → sending → sent / failed / uncertain / skipped. The new design:
  - **Push.** When a job is due, the dashboard sends it to an agent on the
    Home Assistant box, through a Cloudflare Tunnel, so nothing on the shop
    network is opened to the internet.
  - **Direct print.** The agent prints it straight to the printer.
  - **Confirmation.** The agent checks the printer's own page counter and
    status, then calls the dashboard back with the outcome.
- `nmteaco.com` is already on Cloudflare.
- The downtown iPads are staff tablets running a web page from the
  dashboard. They need no changes.
- The dashboard side (sending jobs to the agent, receiving its outcome
  reports, and the shared signing key) is being built separately. Until it
  is live, test the agent with signed requests you make yourself, following
  the contract in Phase 3.

---

## Ground rules

1. **The only way in from outside is the Cloudflare Tunnel**, to one
   hostname that reaches the agent and nothing else. Cloudflare Access
   protects that hostname with a service token. No router port forwards. No
   exposed Home Assistant, printer or other device. Do not put Home
   Assistant's own web interface on this tunnel or hostname.
2. **Don't break the current path.** The printer must keep reaching HP's
   cloud (HP ePrint) for the whole project. The dashboard falls back to
   email for any job the agent never accepted. Do not turn off Web
   Services/ePrint on the printer.
3. **Record before you change.** Before changing a printer, UniFi or
   Cloudflare setting, write down its current value in your report. Change
   only what this document asks for.
4. **Never print a job twice.** A customer order printed twice looks like
   two orders to the staff packing it. The duplicate and restart rules
   below exist to prevent this. Follow them exactly.
5. **Customer data stays put.** A print job contains a customer's name,
   phone number, order items and comments. Never log a job's content, never
   send it anywhere but the printer, and delete each rendered file once it
   has printed. Log only job id, order number, timings, page counts and
   errors.
6. **Secrets.** There are two, and neither goes in source code, logs or
   reports:
   - the **signing key**, which the dashboard generates and the owner pastes
     into the add-on's options;
   - the **Cloudflare Access service token** (client id and secret), which
     the owner enters on the dashboard.

   Do not use any other dashboard credential.
7. If something here turns out to be impossible or wrong for this hardware
   or account, **stop and report** rather than working around it.

---

## Phase 1: survey (read-only, change nothing)

Find out and report:

1. **Network layout.** Which site and network/VLAN the Home Assistant box
   is on, and which the downtown printer is on. Can the Home Assistant box
   reach the printer's IP at all (ping or TCP connect)? If they are at
   different sites, say whether the sites are already joined by a UniFi
   site-to-site VPN. Do not create one yet.
2. **Printer identity.** Exact make and model, firmware version, MAC
   address, current IP and whether that IP comes from DHCP. Use the UniFi
   client list, then the printer's own web page at `http://<printer-ip>/`.
3. **Printer protocols.** Check which of these answer, and include the raw
   output:
   - **IPP** on port 631 (`ipp://<ip>/ipp/print`, or the path the printer
     advertises). Run `Get-Printer-Attributes` and include
     `document-format-supported`, `printer-state`, `printer-state-reasons`,
     `printer-make-and-model`, `urf-supported` and any attribute whose name
     contains `impressions`, `sheets` or `pages`.
   - **Bonjour/mDNS** advertisements: `_ipp._tcp`, `_ipps._tcp`,
     `_pdl-datastream._tcp`.
   - **Raw printing** on port 9100: is it open? Do not send anything.
   - **SNMP** v1/v2c read with community `public`. Read:
     - `1.3.6.1.2.1.43.10.2.1.4.1.1` (lifetime page count)
     - `1.3.6.1.2.1.25.3.5.1.1.1` (printer status)
     - `1.3.6.1.2.1.25.3.5.1.2.1` (detected error state)
     - the `prtAlert` table, `1.3.6.1.2.1.43.18`

     If SNMP is off, say so; don't turn it on yet.
   - The printer's web page: list the status and usage pages it offers and
     whether it shows a page count there.
4. **Page counter.** Read the lifetime page count. Then ask the owner to
   have staff press Reprint once on any order on the curbside tablet (that
   uses today's email path). Read the counter again a few minutes later. If
   the owner prefers, use the printer's own configuration page: hold Resume
   for about 5 seconds. Report both readings: this proves the counter
   reflects paper.
5. **Home Assistant.**
   - The installation type (Settings → About → "Installation type") and
     version.
   - Whether add-ons (Apps) are available.
   - The CPU architecture (amd64, aarch64, …).
   - Free disk and RAM.
   - Which add-on or container runs cloudflared (the tunnel that serves
     `ha.nmteaco.com`), and that it is set to start on boot.
   - Whether the built-in "Internet Printing Protocol (IPP)" integration
     can add the printer. Adding it is allowed; it is read-only.
6. **Outbound reach and clock.**
   - From the Home Assistant box, confirm HTTPS to
     `https://dashboard.nmteaco.com/` works: any HTTP status counts, since
     it proves the connection.
   - Confirm the box's clock is synchronised (NTP). The requests are signed
     with a timestamp, so the clock matters.
7. **Cloudflare.** The owner has made the Cloudflare changes (Phase 2,
   step 4). You do not need Cloudflare access.

**Send the report back to the owner, then stop.**

---

## Phase 2: network and tunnel (after the owner approves Phase 1)

1. **Fixed addresses, through the UniFi API.** Make the change with the
   UniFi Network API, not by hand in the UniFi web interface, so it can be
   repeated and read back exactly.
   1. **Find the printer.** Look it up in the client list by its MAC
      address (from Phase 1), not by name or by IP. Record the client
      record as it is now: IP, network, and whether it already has a fixed
      IP.
   2. **Pin its current address.** Set that client to a fixed IP on its
      *current* address and network. In UniFi's API this is the client's
      "use fixed IP" setting with the fixed IP and network id. Keeping the
      address it already has means the printer needs no restart and
      nothing that already reaches it breaks. If the current address is
      taken by another reservation, stop and report rather than picking a
      new one.
   3. **Read it back.** Fetch the client again and include the before and
      after values in your report.
   4. **Do the same for the Home Assistant box** if it has no fixed IP
      yet.
   5. **Credentials.** Use an API key or a local account with the least
      access the console offers for this. If you create one, give its name
      in the report but not its key. Change nothing else in UniFi.
2. **Box-to-printer access.** If the Home Assistant box is not on the
   printer's network, make the printer reachable from it with the least
   access possible: allow only these, from the Home Assistant box to the
   printer IP:
   - TCP 631 (IPP)
   - TCP 9100 (raw), only if Phase 1 showed IPP is unusable
   - UDP 161 (SNMP)
   - TCP 80 (printer web page)

   If they are at different sites with no VPN between them, stop and
   report: the owner decides whether to move the box or join the sites.
3. **SNMP.** If SNMP was off and IPP gives no page count, turn on
   **read-only** SNMP v1/v2c on the printer with community `public`. Do it
   on the printer's web page and record that you did. Change no other
   printer setting.
4. **Cloudflare (done by the owner already).** The owner has set up:
   - a service token `dashboard-print`;
   - an Access application `Downtown printer` for
     `print-downtown.nmteaco.com` with a Service Auth policy admitting only
     that token;
   - a published application route on the existing tunnel (the one that
     carries `ha.nmteaco.com` → `http://homeassistant:8123` and
     `ssh-ha.nmteaco.com` → `ssh://core-ssh:22`):
     `print-downtown.nmteaco.com` → `http://local-nmt-print-agent:8080`.

   Do not change any of these. Build the add-on so it listens on port 8080
   with the slug `nmt_print_agent`, which gives it the hostname
   `local-nmt-print-agent` on the add-on network that the route points at.
   If Home Assistant gives the add-on a different hostname, report it: the
   owner changes the route. Confirm from the Home Assistant box that
   `http://local-nmt-print-agent:8080` answers once the add-on runs, and
   that `https://print-downtown.nmteaco.com/status` without the token is
   refused by Cloudflare.

---

## Phase 3: the print agent (a Home Assistant add-on)

### Packaging

- Build a **local Home Assistant add-on** called `nmt_print_agent` (in
  `/addons/local/nmt_print_agent` on the box): `config.yaml`, `Dockerfile`
  on the Home Assistant base image for the box's architecture, and the
  agent itself. Use Python unless the base image makes something else
  clearly better.
- If the install type has no add-ons (Container or Core), run the same
  container beside Home Assistant with `restart: unless-stopped` instead.
- Settings:
  - `boot: auto` and a watchdog or restart policy, so it starts after a
    reboot and restarts if it crashes.
  - Logs go to the add-on log.
  - State is kept in `/data` (survives restarts).
- **The agent's web server listens on one port.** It is reachable only from
  cloudflared and the local network; not mapped to the internet any other
  way.
- Options (in `/data/options.json`):
  - `dashboard_url` (default `https://dashboard.nmteaco.com`)
  - `signing_key` (password type; the owner pastes it)
  - `agent_name` (default `downtown`)
  - `printer_uri` (from Phase 1)
  - `printer_ip`
  - `snmp_community` (default `public`)
  - `listen_port`
- **Converting the print.** The dashboard sends HTML. Render it to a
  Letter-size PDF inside the container (WeasyPrint or similar; no browser
  needed). Then print through **CUPS in the container**, with a driverless
  queue (`lpadmin -p downtown -v <printer_uri> -m everywhere`), so CUPS
  converts the PDF into whatever the printer takes: PDF, PWG raster or
  URF/AirPrint. Only if the printer has no working IPP, fall back to an
  HP-specific driver (HPLIP) on port 9100. Many small HP lasers accept only
  raster, so do not send PDF straight to the printer unless Phase 1 showed
  `application/pdf` in `document-format-supported`.

### Signing (both directions)

Every request between the dashboard and the agent, in either direction,
carries:

```
X-NMT-Timestamp: <unix seconds>
X-NMT-Signature: <hex HMAC-SHA256(signing_key, timestamp + "\n" + METHOD + "\n" + PATH + "\n" + raw body)>
```

- Reject a request whose timestamp is more than 300 seconds from your
  clock, or whose signature does not match (constant-time comparison).
  Answer 401 and do nothing else.
- Requests to the agent through the tunnel also carry Cloudflare's service
  token headers (`CF-Access-Client-Id`, `CF-Access-Client-Secret`).
  Cloudflare checks those; the agent checks the signature.

### What the dashboard pushes to the agent

**`POST /print`**, body:

```json
{"id": 812, "order": "T-99460", "kind": "auto|reprint|test", "html": "<!doctype html>..."}
```

A `test` job is a sample page with no customer data. The owner can queue
one from the dashboard; use it for every test.

The agent answers **within 5 seconds**. It does not wait for the paper.

| Answer | Meaning |
|---|---|
| `202 {"accepted": true}` | Queued on the agent; it will print and report back. From this moment the job belongs to the agent, and the dashboard never sends it by email. |
| `200 {"accepted": true, "duplicate": true, "outcome": "printed\|failed\|uncertain\|pending"}` | This job id was received before. **Do not print it again**; answer with what happened to it. Keep the last 1,000 job ids and their outcomes in `/data`. |
| `503 {"accepted": false, "reason": "media-empty"}` | The printer cannot print right now. Use the printer's own reasons: `media-empty`, `media-jam`, `door-open`, `offline`, `unreachable`. Nothing is queued; the dashboard keeps the job and tries again later. |
| `401` | Signature or clock problem. |

**`GET /status`** (signed, empty body). The dashboard calls it to show the
printer's state on the staff tablet. Answer:

```json
{
  "agent": "downtown",
  "version": "1.0.0",
  "printer": {
    "reachable": true,
    "state": "idle | processing | stopped",
    "reasons": ["media-empty"],
    "page_count": 12345
  },
  "queue": 0
}
```

### What the agent sends back to the dashboard

When a job finishes, send `POST <dashboard_url>/tools/orders/curbsideAgent.php`,
signed, with:

```json
{
  "action": "result",
  "agent": "downtown",
  "id": 812,
  "outcome": "printed | failed | uncertain",
  "pages_before": 12345,
  "pages_after": 12346,
  "error": "media-jam"
}
```

What each outcome means:

- `printed`: the printer finished the job **and** its page counter went up
  by at least 1.
- `failed`: the printer refused the job, or nothing printed and the printer
  reports why. Put the reason in `error`. The dashboard will retry by
  pushing it again under a **new** job id and shows staff the banner.
- `uncertain`: you cannot tell. For example, the job was sent but the
  counter could not be read, or the agent restarted mid-job. The dashboard
  does **not** resend an uncertain job on its own; staff decide.

How to send it:

- The dashboard answers `{"ok": true}`. If you get anything else (network
  error, 5xx), keep the result in `/data` and retry with backoff (5 s,
  30 s, 2 min, then every 5 min) until the dashboard accepts it.
- The dashboard treats a repeated result for the same id as already
  recorded.

### Agent behaviour

1. **Queue.** Accepted jobs go into a queue kept in `/data`, printed one at
   a time, oldest first.
2. **On start:** if `/data` shows a job that was being printed (sent to
   CUPS but not finished), report it `uncertain` with
   `error: "agent restarted during the job"`. Never print it again. Then
   continue with the rest of the queue.
3. **For each job:**
   1. Mark it in progress in `/data` *before* printing.
   2. Render the HTML to PDF in a temporary file.
   3. Read the page counter (`pages_before`).
   4. Submit the job to CUPS.
   5. Wait until CUPS reports the job completed or aborted, at most
      3 minutes.
   6. Poll the page counter until it is stable for 10 seconds, at most
      60 seconds after completion (`pages_after`).
   7. Decide the outcome from the rules above and report it.
   8. Delete the PDF and record the outcome against the job id.
4. **Blocked printer.** If the printer reports a blocking reason while jobs
   are queued, hold the queue. Do not submit to CUPS while the printer
   can't print. Report each held job `failed` with that reason only after
   10 minutes of being blocked, so staff are told and the dashboard can
   retry or fall back.
5. **Watch the printer.** Read its status about every 30 seconds whether
   or not there is a job, so `GET /status` answers from fresh data.
6. **Logging:** one line per request (method, path, job id, answer code)
   and one per job: id, order number, outcome, page delta, seconds taken,
   error. Never the HTML, never a header value.

---

## Phase 4: tests (all with `test` jobs unless stated)

Make the signed requests yourself until the dashboard side is live. Report
each result.

1. **Normal print.** Push one test job: answer 202 in under 5 seconds, one
   sheet out, result `printed` with page delta 1.
2. **Duplicate.** Push the same job id again. Expect `duplicate: true` and
   no second sheet.
3. **Bad signature, old timestamp, no Cloudflare token.** Expect each
   refused (401, or Cloudflare's refusal), nothing printed.
4. **Out of paper.** Remove the paper and push a test job. Expect 503
   `media-empty`. Put the paper back, push again under a new id: exactly
   one sheet.
5. **Jam or door open mid-queue.** Queue two jobs and open the door after
   the first. Expect the second held, then `failed` with the reason after
   10 minutes if still blocked, or printed once if the door is closed
   sooner.
6. **Printer powered off.** Expect `/status` to show `reachable: false` and
   `/print` to answer 503 `unreachable`.
7. **Restart mid-job.** Restart the add-on while a job is printing. Expect
   that job reported `uncertain` on start and never printed again.
8. **Reboot the Home Assistant box.** Expect cloudflared and the agent back
   by themselves, and `/status` answering through the tunnel.
9. **Dashboard unreachable.** Block the box's access to the dashboard,
   print a job, then unblock. Expect the result delivered once access
   returns.
10. **Real order.** Arrange with the owner for staff to stand at the
    printer and press Reprint on one real order on the curbside tablet.
    Expect exactly one sheet, `printed`, and the staff member confirming
    what came out matches the order.

---

## What to send back

- **The Phase 1 report**, raw outputs included.
- **Every setting changed**, before and after: the UniFi API calls you made
  and what each read-back returned, firewall rules, and printer SNMP. No
  secrets.
- **The printer's fixed IP**, so the owner can enter it on the curbside
  tablet's Fix Printer Issues page.
- **The add-on's complete source** (`config.yaml`, `Dockerfile`, agent
  code) as files, so it can be kept in the shop's code repository.
- **The Phase 4 results.**
- **Anything in this document that was wrong for this hardware or
  account**, and what you did instead.
