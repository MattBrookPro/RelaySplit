# RelaySplit

Low-latency audio transport over **WebRTC**, with GPU processing running on **Modal**, a
signalling/control server running on a **VPS**, and (later) a **JUCE plugin** acting as a
native WebRTC client. The thesis: keep every network hop as short as possible so a remote
GPU can sit inside a real-time audio path.

## Components

| Component | Where it runs | Status |
|-----------|---------------|--------|
| **Modal GPU peer** | Modal (region `uk`) | ✅ **LIVE** — real-time browser↔GPU WebRTC vocal separation, Demucs v4, with latency meter ([`gpu/relaysplit_live.py`](gpu/relaysplit_live.py)) |
| **Signalling / control server** | VPS, under `pm2` on `:8080` | ✅ live (nginx TLS → `:8080`) — [`server/`](server/) |
| **TURN relay** | VPS (coturn) | ✅ as-built; ephemeral creds minted by the server |
| **Web app** (login / channels / presence / launch) | served by VPS nginx | ✅ live at the root ([`server/public/index.html`](server/public/index.html)) |
| **JUCE plugin** (WebRTC client + latency meter) | this Windows workstation | 🟡 foundation builds — VST3 + Standalone ([`plugin/`](plugin/)); WebRTC client next |

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

1. ✅ **Modal WebRTC peer** deployed (spike), region `uk`.
2. ✅ **Spike WebRTC → Modal** proven — audio round-trip, `relay ↔ relay`.
3. ✅ **Node signalling/control server** live on the VPS ([`server/`](server/), pm2 + nginx TLS).
4. ✅ **Browser ↔ Modal LIVE** — real-time vocal separation through the GPU with a latency meter ([`gpu/relaysplit_live.py`](gpu/relaysplit_live.py)).
5. ✅ **Separation model** — Demucs v4 on GPU, streaming 0.25 s chunk / 5 s context / 20 ms crossfade, real-time.
6. ✅ **Accounts/sessions/channels** (SQLite) + ✅ one-click always-on **`/demo`** feed.
7. 🟡 **JUCE plugin** foundation builds ([`plugin/`](plugin/)); WebRTC client designed + DAW-gated ([`plugin/PHASE2.md`](plugin/PHASE2.md)).
8. ⏳ Remaining: plugin WebRTC client (build libdatachannel), container↔`/ws` session, hub/receiver/sharing UI.

## Repo layout

```
README.md                  this file
LEARNING.md                interview-prep companion (requirement → code map)
.gitignore  .gitattributes
spike/                     Step 0 spike — WebRTC audio round-trip through Modal (PASSED)
server/                    control plane — signalling, presence, ephemeral TURN (live on VPS)
docs/
  setup-machine2.md        as-built setup record for the Windows dev workstation
(planned)  modal/  plugin/  web/
```

## Setup

See [`docs/setup-machine2.md`](docs/setup-machine2.md) — Modal CLI auth, the verified
EU region pin, WebRTC test tooling, and the VPS-side facts to build against.
