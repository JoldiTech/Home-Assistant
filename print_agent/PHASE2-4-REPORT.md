# Downtown print agent: Phases 2–4

Done 2026-10-11 (MT morning, store closed). Phase 1 report approved by the
owner first. Contract: `INSTRUCTIONS.md`.

**Printer fixed IP for the curbside tablet's Fix Printer Issues page:
`192.168.22.238`.**

## Phase 2: network and tunnel

### Settings changed

| What | Before | After |
| --- | --- | --- |
| UniFi client `5c:fb:3a:0d:80:7e` (`_id 659728f49a62e95d43422b61`, hostname NPI02EFF1): `use_fixedip` | unset | `true` |
| … `fixed_ip` | unset | `192.168.22.238` |
| … `network_id` | unset | `5e13358d4c7ad505143f64ac` (Default) |
| … `noted` | unset | `false` (added by UniFi on the write, no effect) |

Nothing else changed, in UniFi or anywhere else in Phase 2.

**Call** (from the AI box, key from `/etc/nmteaco/protect.env`, not shown):

```
PUT https://192.168.22.1/proxy/network/api/s/default/rest/user/659728f49a62e95d43422b61
{"use_fixedip":true,"fixed_ip":"192.168.22.238","network_id":"5e13358d4c7ad505143f64ac"}
→ HTTP 200 {"meta":{"rc":"ok"}, …}
```

**Before** (GET of the same record): `last_ip 192.168.22.238`,
`last_connection_network_id 5e13358d…` (Default), Wi-Fi (`is_wired false`,
uplink U7 Pro Max), no `use_fixedip`/`fixed_ip`/`network_id` keys. The live
client list showed the printer at .238, connected for ~19.6 days.

**Pre-checks:** looked up by MAC, not name or IP. Fixed IPs on the site before
the change: DesignPC .192, homeassistant .254, Ai Server .6. Nobody held .238.

**Read-back:** a fresh GET returns `use_fixedip true`, `fixed_ip
192.168.22.238`, `network_id 5e13358d4c7ad505143f64ac`; the site's fixed-IP list
now has four entries including the printer; the printer answered ping straight
after. Its address didn't change, so no restart was needed.

### Not changed (not needed)

- **HA box IP:** already a UniFi fixed IP (192.168.22.254).
- **Firewall:** HA box and printer share the Default network (192.168.22.0/24).
- **SNMP:** already on, read-only community `public`.
- **Cloudflare:** the owner's setup, untouched.

### Credentials

The existing console API key that the AI box already uses for Protect and
Network (`/etc/nmteaco/protect.env`). No new key or account was created. UniFi
API keys aren't scoped (the same key works for Protect, Network and the legacy
API), so a less-privileged key isn't available. A local read/write account
limited to Network would be the narrowest option if the owner wants to separate
it.
