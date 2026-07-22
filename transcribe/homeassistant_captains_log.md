# Home Assistant side — Captain's Log trigger + display

HA owns the schedule and triggers the AI box over the LAN. The AI box does the
transcription + de-identified summary — plus the business-day data (sales,
shipping, support, calls, texts, timeclock via the dashboard datalog API, and
Slack staff chat) — and pushes the log to the private GitHub repo. HA reads it
back from GitHub for the dashboard.

AI-box keys in `/etc/nmteaco/captains.env` (mode 600): `TRIGGER_TOKEN`,
`GITHUB_TOKEN`, `DATALOG_API_TOKEN` (same value as the dashboard's
`DATALOG_API_TOKEN` in `/home/nmteaco/.env` — see the API Credentials page),
optional `DASHBOARD_BASE_URL`, `SLACK_BOT_TOKEN`, `SLACK_CHANNELS`. See
`captains_log/README.md` for what each feeds.

## 1. Trigger the AI box (add to `/config/configuration.yaml`)

The AI box and HA are on the same LAN, so this is a direct HTTP call — no
Cloudflare. Replace the host with the AI box's LAN IP, and put the shared secret
(same `TRIGGER_TOKEN` as `/etc/nmteaco/captains.env` on the AI box) in
`secrets.yaml`.

```yaml
rest_command:
  captains_log_run:
    url: "http://192.168.22.6:8190/run"   # AI box LAN IP
    method: POST
    headers:
      X-Trigger-Token: !secret aibox_trigger_token
      Content-Type: application/json
    # Empty date => AI box uses today (America/Denver). Pass a date to backfill.
    payload: '{"date": "{{ date | default("", true) }}"}'
```

## 2. Fire it on a schedule you control (add to `automations.yaml`)

Edit the time / enable-toggle right here in HA — that's the whole point of
HA owning the trigger.

```yaml
- alias: "Captain's Log — nightly transcription"
  trigger:
    - platform: time
      at: "19:00:00"        # 7pm Mountain; change freely
  action:
    - service: rest_command.captains_log_run
```

## 3. Show it on the dashboard, read from GitHub

The AI box pushes each day's log to `captains_log/<date>.md` on the
`captains-log` branch. HA fetches the day's file from the GitHub API with a
read token (fine-grained PAT, `contents:read` on the repo) in `secrets.yaml` as
`github_read_token`. GitHub returns the content base64-encoded, which a template
decodes:

```yaml
# configuration.yaml
sensor:
  - platform: rest
    name: Captains Log Today
    unique_id: captains_log_today
    resource_template: >-
      https://api.github.com/repos/JoldiTech/Home-Assistant/contents/captains_log/{{ now().strftime('%Y-%m-%d') }}.md?ref=captains-log
    headers:
      Authorization: !secret github_read_token   # value: "Bearer github_pat_..."
      Accept: application/vnd.github.raw
      X-GitHub-Api-Version: "2022-11-28"
    scan_interval: 3600
    value_template: "{{ 'ok' if value else 'none' }}"
    json_attributes:
      - content            # with Accept: raw, GitHub returns the file body directly
```

(With `Accept: application/vnd.github.raw` GitHub returns the raw markdown, so no
base64 decode is needed. A markdown card renders `state_attr('sensor.captains_log_today','content')`.)

This replaces the old `/share`-based `render_captains_log.py` + `command_line`
sensor once the pipeline is live: GitHub becomes the single source of truth.
```
