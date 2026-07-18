# Home-Assistant

Tools and notes for querying the **New Mexico Tea Company** Home Assistant
instance (cameras, sensors, automations). This repo is not the HA config itself
— it's a toolbox for talking to the live instance over its REST API.

## Connecting (read this first)

The session environment provides two variables — **never commit the token**:

| Variable | Value |
|---|---|
| `HOMEASSISTANT_BASE_URL` | `https://ha.nmteaco.com` |
| `HOMEASSISTANT_TOKEN` | long-lived access token (secret, from env only) |

Quick connectivity check:

```bash
curl -sS -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" "$HOMEASSISTANT_BASE_URL/api/"
# => {"message":"API running."}
```

- **HA version:** 2026.7.x
- **Timezone:** `America/Denver` (Mountain Time). The API returns timestamps in
  **UTC**; convert to Mountain for anything shown to a human.
- Cameras are **UniFi Protect**. AI detections surface as
  `binary_sensor.<camera>_<type>_detected` (person, vehicle, animal, etc.) and
  are toggled by matching `switch.<camera>_<type>_detection` entities.

## Common task: "when was a human last seen on the cameras?"

Just run the script — it answers in one API call:

```bash
./scripts/last_person_seen.py            # who's on camera now / when last seen
./scripts/last_person_seen.py --list     # every person-detection camera + state
./scripts/last_person_seen.py --detail   # recent detection windows (movement path)
./scripts/last_person_seen.py --detail 12  # look back 12 hours
```

"A human was seen" == a `binary_sensor.*_person_detected` sensor was `on`. If one
is `on` now, someone is on camera live; otherwise its `last_changed` is when the
most recent detection cleared (≈ last seen). `--detail` reconstructs on→off
intervals from the history API to show the path a person took between cameras.

## Person-detection cameras

⚠️ **Entity IDs do NOT match friendly names.** Always map via `friendly_name`
(what a human calls the camera), not the entity prefix. This mismatch is the #1
time-sink — don't guess from the entity id.

| Location (friendly name) | Person-detected entity |
|---|---|
| Emporium Floor | `binary_sensor.tea_two_person_detected` |
| Emporium Hall | `binary_sensor.emporium_hall_person_detected` |
| Tea One | `binary_sensor.g6_dome_person_detected` |
| Tea Two Camera | `binary_sensor.tea_two_neo_person_detected` |
| Packing Station | `binary_sensor.packing_station_person_detected` |
| Store Room | `binary_sensor.store_room_person_detected` |
| Back Yard | `binary_sensor.g6_180_person_detected` |
| Tea One (secondary, often offline) | `binary_sensor.tea_one_person_detected` |

Motion-only cameras (no person AI): **Kitchen**, **Curbside / Backdoor**,
**12th Street Emporium**. Each camera also exposes `_motion`, `_vehicle_detected`,
`_animal_detected`, plus audio detections (glass break, smoke/CO alarm, etc.).

## Useful raw API calls

```bash
# All person sensors, newest change first (the fast path the script uses):
curl -sS -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" \
  "$HOMEASSISTANT_BASE_URL/api/states" \
  | jq -r '.[] | select(.entity_id|endswith("_person_detected"))
      | "\(.last_changed)\t\(.state)\t\(.attributes.friendly_name)"' | sort -r

# History for one entity since a UTC timestamp:
curl -sS -G -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" \
  --data-urlencode "filter_entity_id=binary_sensor.tea_two_person_detected" \
  "$HOMEASSISTANT_BASE_URL/api/history/period/2026-07-18T00:00:00+00:00"

# Instantaneous state of a single entity:
curl -sS -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" \
  "$HOMEASSISTANT_BASE_URL/api/states/binary_sensor.tea_two_person_detected"
```

Notes on the history endpoint: pass `minimal_response` to shrink payloads, but be
aware it **omits `entity_id` on repeated rows** — don't use it when you need to
know which camera each row belongs to (the script fetches full history for that
reason).

## Conventions for this repo

- Scripts live in `scripts/`, are standard-library-only Python 3, and read
  credentials from the env vars above — no secrets in code.
- Keep this file current when you learn something new about the instance
  (new cameras, renamed entities, retention limits) so the next session starts
  fast instead of re-discovering it.
