# Changelog

## 1.4.0

- CryptoCart's register box runs beside the agent as a second s6 service:
  box 1.5.0, `box.mjs` built from CryptoCart `2cf4c2f2` (
  `main`) with `npm run build:box -w apps/pos`,
  SHA-256 `68a6701c94005fb8e49c78649fa3dbcfcb670ed978cd41754a61f5e8cfe3e1e9`,
  on Node 26.12.1.
- New option `cryptocart_company` (empty: the box stays off). Host port 8443
  for registers on the shop's Wi-Fi. `stdin` carries the box's pairing code.
- The box stays down, logged once, when it isn't set up (exit 0) or refuses
  its configuration (exit 78); any other exit restarts it.
- The agent runs as an s6 service instead of the image's command; its
  behaviour is unchanged.
