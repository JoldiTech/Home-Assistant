# Home-Assistant

Tools for querying the New Mexico Tea Company Home Assistant instance
(`https://ha.nmteaco.com`) over its REST API.

## Quick start

Set the two environment variables the tools expect (provided by the session
environment — keep the token secret, never commit it):

```bash
export HOMEASSISTANT_BASE_URL=https://ha.nmteaco.com
export HOMEASSISTANT_TOKEN=<long-lived-access-token>
```

Then ask when a person was last seen on the cameras:

```bash
./scripts/last_person_seen.py            # who's on camera now / when last seen
./scripts/last_person_seen.py --list     # every person-detection camera + state
./scripts/last_person_seen.py --detail   # recent detection windows (movement path)
```

## What's here

- **`scripts/last_person_seen.py`** — reports live/last human detection from the
  UniFi Protect "Person detected" AI sensors.
- **`CLAUDE.md`** — system reference: connection details, timezone, the
  camera→entity map, and raw API examples. Read this first when working here.
