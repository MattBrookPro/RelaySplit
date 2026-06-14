# RelaySplit

Low-latency audio transport over **WebRTC**, with GPU processing running on **Modal**, a
signalling/control server running on a **VPS**, and (later) a **JUCE plugin** acting as a
native WebRTC client. The thesis: keep every network hop as short as possible so a remote
GPU can sit inside a real-time audio path.

## Components

| Component | Where it runs | Status |
|-----------|---------------|--------|
| **Modal GPU peer** | Modal (region `uk`) | 🟢 transport PASSED ([`spike/`](spike/)); **Demucs v4** separation on GPU, quality confirmed ([`gpu/`](gpu/)) — streaming latency path next |
| **Signalling / control server** | VPS, under `pm2` on `:8080` | ✅ live (nginx TLS → `:8080`) — [`server/`](server/) |
| **TURN relay** | VPS (coturn) | ✅ as-built; ephemeral creds minted by the server |
| **Web client** (`/login`, `/app`) | served by VPS nginx | 🟡 minimal landing page; full client later |
| **JUCE plugin** (WebRTC client + latency meter) | this Windows workstation | ⏳ deferred — Phase 4 |

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
4. 🟡 **Browser ↔ Modal** proven via the spike; multi-peer sessions over the server next.
5. 🟡 **Separation model**: pivoted to **Demucs v4** on GPU (quality confirmed); latency sweep done (~365 ms floor + network on L4); real-time aiortc wiring next.
6. ⏳ **JUCE plugin** as WebRTC client + latency meter, then accounts/channels/hub/receiver.

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
