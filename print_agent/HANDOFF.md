# Handoff: downtown print agent (after Phases 2–4, 2026-10-11)

- **Plan/contract:** `INSTRUCTIONS.md`. **Phase 1:** `PHASE1-REPORT.md`.
  **What was done and tested:** `PHASE2-4-REPORT.md`. Running notes in the
  CLAUDE.md "Downtown print agent" section.
- **State:** the printer is pinned at 192.168.22.238 (UniFi). The add-on
  `local_nmt_print_agent` v1.0.0 runs on the HA Green (boot auto, watchdog on,
  signing key set). Live tests 1, 2, 3, 7 and 8 pass.

## Remaining

1. Tests 4, 5, 6 (paper out, door open mid-queue, printer off): need someone at
   the printer. Push jobs with `tests/remote_request.py print <id>`, using ids
   from **2100000010 upward** (2100000001 and 2100000007 are used). Never use
   a small id: it could collide with a real dashboard job id.
2. Test 10 (real Reprint from the curbside tablet): after the dashboard side is live.
3. A 200 for `/status` *through* the tunnel: needs the `dashboard-print` token as
   `CF_ACCESS_CLIENT_ID_PRINT` / `CF_ACCESS_CLIENT_SECRET_PRINT`, or a check
   from the dashboard.
4. Test 9 live: once `curbsideAgent.php` exists, the two queued test results
   (ids 2100000001, 2100000007) should deliver within 5 minutes. Check the
   add-on log for `result job … delivered`.

## Rules carried over

- Never log job HTML or header values. Never print a job twice. Leave HP
  ePrint on. `ip_ban` is on HA: no auth retry loops. Don't touch store lights
  or music.
- Supervisor `/addons/<slug>/options` replaces the whole options object. For
  this add-on, always send every option including `signing_key` (via stdin,
  never on a command line), or the key is wiped.
