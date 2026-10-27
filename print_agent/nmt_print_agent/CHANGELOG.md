# Changelog

## 1.4.1

- Register box 1.6.0, `box.mjs` built from CryptoCart `main` at `e5c76433`
  with `npm run build:box -w apps/pos`, SHA-256
  `a07fda2ca4486d5c629c78f55d30cda207749b920f2727e5347a0b31d3376d8f`, on Node
  26.12.1. The box now looks on its own /24 (from `lan_ip`) for counter
  scanner adapters (TCP 10001) and receipt printers (TCP 9100), connecting
  and closing without a byte, and reports them to POS to be set up there.

## 1.4.0

- CryptoCart's register box runs beside the agent as a second s6 service:
  box 1.5.0, `box.mjs` built from CryptoCart `main` at `2cf4c2f2` with
  `npm run build:box -w apps/pos`,
  SHA-256 `68a6701c94005fb8e49c78649fa3dbcfcb670ed978cd41754a61f5e8cfe3e1e9`,
  on Node 26.12.1.
- New option `cryptocart_company` (empty: the box stays off). Host port 8443
  for registers on the shop's Wi-Fi. `stdin` carries the box's pairing code.
- The box stays down, logged once, when it isn't set up (exit 0) or refuses
  its configuration (exit 78); any other exit restarts it.
- The agent runs as an s6 service instead of the image's command; its
  behaviour is unchanged.
