# RelaySplit

Low-latency audio transport over **WebRTC**, with GPU processing running on **Modal**, a
signalling/control server running on a **VPS**, and (later) a **JUCE plugin** acting as a
native WebRTC client. The thesis: keep every network hop as short as possible so a remote
GPU can sit inside a real-time audio path.

## Components

| Component | Where it runs | Status |
|-----------|---------------|--------|
| **Modal GPU function** | Modal (region-pinned, see below) | to build — Phase 1 |
| **Signalling / control server** | VPS, under `pm2` on `:8080` | runs on the VPS, **not** locally |
| **TURN relay** | VPS (coturn) | as-built |
| **Web client** (`/login`, `/app`) | served by VPS nginx | to build |
| **JUCE plugin** (WebRTC client + latency meter) | this Windows workstation | deferred — Phase 4 |

### Latency-critical fact

The latency-critical path is a **UK↔UK** connection to Modal, so the Modal GPU function pins
**`region="uk"`**. The Helsinki VPS (`relaysplit.vaguelystrange.com`, Hetzner FI) handles a
**secondary** connection — it does **not** drive the Modal region. See
[`docs/setup-machine2.md`](docs/setup-machine2.md) for the fallback chain and rationale.

## Roles by machine

- **This machine (Windows workstation):** development, Modal CLI driver, and later the JUCE
  plugin host. It does **not** run the Node server.
- **VPS (`relaysplit.vaguelystrange.com`):** signalling/control server, TURN, nginx + certbot.
- **Modal:** the region-pinned GPU container the WebRTC stream targets.

## Build order (dependency-correct)

1. Write + deploy the **Modal script** (the gate — no deployed script, no container URL).
2. **Spike WebRTC → Modal** in isolation (the one genuinely uncertain part).
3. **Node signalling/control server** on the VPS.
4. **Browser ↔ browser** WebRTC through VPS signalling + TURN.
5. **Browser ↔ Modal** WebRTC (the moment of truth).
6. **JUCE plugin** as WebRTC client, then accounts/channels/hub/receiver.

## Repo layout

```
README.md                  this file
.gitignore
docs/
  setup-machine2.md        as-built setup record for the Windows dev workstation
```

## Setup

See [`docs/setup-machine2.md`](docs/setup-machine2.md) — Modal CLI auth, the verified
EU region pin, WebRTC test tooling, and the VPS-side facts to build against.
