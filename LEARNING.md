# RelaySplit — LEARNING.md

Interview-prep companion for the **Audiomovers** flagship. For each requirement: what it is,
**where in this repo it lives**, what the code does and *why*, and a rehearsed one-liner.

> Status legend: ✅ built & verified · 🟡 partial · ⏳ planned (honest — not yet built).
> Never claim a ⏳ row as done in an interview; the "Address in conversation" section covers gaps.

---

## The one idea to lead with: control plane vs data plane

RelaySplit splits cleanly into two planes, and almost every design decision falls out of it:

- **Control plane = the VPS** ([`server/`](server/)). Signalling, presence, channels, auth,
  and **ephemeral TURN minting**. It is a fast courier for *negotiation*; **audio never flows
  through it**. See [`server/src/index.ts:12`](server/src/index.ts) — the composition root wires
  REST + signalling onto one port and nothing else.
- **Data plane = WebRTC**, the shortest path available for each case (peer-to-peer, TURN-relayed,
  or — for the always-on hub — server-relayed as a deliberate latency-for-availability trade).

> **One-liner:** *"The server runs the peer system and signalling; the audio takes the shortest
> route it can and never passes through the server — that's how you keep latency honest."*

---

## Requirement → teachable-moment map

### ✅ Signalling + control plane (Node/TS)
- **(b)** [`server/src/signalling.ts:34`](server/src/signalling.ts) `attachSignalling()`; the pure
  relay is the `signal` branch at [`signalling.ts:73`](server/src/signalling.ts). REST surface in
  [`server/src/api.ts:14`](server/src/api.ts).
- **(c)** Two peers can't talk directly until they've swapped SDP offers/answers and ICE
  candidates — but that exchange is *what they need a third party for*. The server mounts a
  WebSocket at `/ws`, assigns each connection a server-side id (so a client can't spoof another
  peer), tracks who's in each room, and forwards the opaque SDP/ICE blobs to the addressed peer.
  It never parses the audio negotiation — it just routes it.
- **(d)** *"Signalling is a dumb fast relay on the control plane: it forwards SDP/ICE between
  peers and assigns identity server-side; the media negotiates around it, not through it."*

### ✅ STUN / TURN / NAT traversal
- **(b)** [`server/src/turn.ts:26`](server/src/turn.ts) `mintTurnCredential()`; consumed by
  [`POST /api/turn`](server/src/api.ts) at `api.ts:21`.
- **(c)** A serverless GPU container (and most clients) sit behind NAT with no public inbound, so
  WebRTC needs a TURN relay both sides dial *out* to. TURN relay costs money, so it authenticates.
  Handing clients the long-term secret would be fatal; instead we derive a short-lived credential
  server-side: `username = "<unix-expiry>:<label>"`, `credential = base64(HMAC-SHA1(secret,
  username))`. coturn recomputes the same HMAC and rejects expired usernames. We validated this
  scheme by hand on the VPS before writing the server.
- **(d)** *"TURN guarantees connectivity behind strict NAT; I issue ephemeral HMAC credentials
  per session so the long-term secret never leaves the server and a leak is dead in minutes."*

### ✅ WebRTC to a *server-side* peer (the differentiator)
- **(b)** [`spike/relaysplit_spike.py`](spike/relaysplit_spike.py) — an `aiortc` peer running
  **inside a Modal container** (`on_track` echoes audio back via `MediaRelay`); ICE config built
  from the same STUN+TURN servers.
- **(c)** The hard, non-obvious part of the whole project: making a serverless cloud GPU container
  act as a WebRTC *peer* (gather ICE, allocate a TURN relay, exchange media), not just a request
  handler. Proven end-to-end — a browser's audio round-trips through the container and back, path
  `relay ↔ relay` via the VPS TURN server. (Gotcha learned: the offerer must wait for ICE
  gathering to complete before sending the offer, or the peer installs no TURN permission and ICE
  fails — see the spike's `waitForIceGathering`.)
- **(d)** *"Audio runs over WebRTC all the way to the GPU container, not just between users — the
  container is a real ICE peer. That's the thing that makes datacenter-GPU audio feel local."*

### ✅ Full-stack web + infra
- **(b)** [`server/`](server/) deployed on the VPS under **pm2**, fronted by **nginx + certbot**
  (HTTPS/WSS), with **coturn** for relay. [`server/scripts/ws-smoke.cjs`](server/scripts/ws-smoke.cjs)
  is the relay regression test.
- **(c)** It's actually live: TLS-terminated by nginx, proxied to a Node process on `:8080`, on a
  shared Linux box, with a TURN server beside it. Health/credentials/signalling all reachable over
  the public internet.
- **(d)** *"It's live on a UK box over HTTPS with a TURN relay; I deploy it under pm2 and there's a
  smoke test that asserts the signalling relay still routes."*

### 🟡 Full latency story (instrumentation) — *control-plane half done; meter is ⏳*
- TURN/region/warm-start are in place conceptually; the live network-RTT-vs-inference **meter** is
  not built yet (data-plane work). Don't claim the meter until step 6 lands.

### ⏳ C++ / JUCE plugin · audio-thread discipline · real-time GPU inference (causal model) · live latency meter
- Not built yet. Planned per the brief's spine (steps 2–4, 6). The spike proves the transport they
  sit on; the plugin replaces the browser client, and the Modal container swaps passthrough for a
  causal separation model. **Interview-honest:** "the transport and control plane are proven and
  live; the plugin and the model are the next slices."

---

## Address in conversation, not code

- **FastCGI** — part of their stack; not built here. Honest: "haven't used it; it's a CGI variant
  I'd pick up — my web serving here is Node behind nginx."
- **SFU / scaling** — the demo is mesh/relay (fine for a few peers). "Production fan-out is an SFU
  like mediasoup; I've scoped that but not built it — direct paths are right for the demo."
- **so-vits-svc / prior GPU-audio** — real backing for "I've done low-latency neural audio on GPU."
  Be precise: the *proven low-latency run was local*; **this project's achievement is doing it over
  the network** via a region-pinned, warm Modal container.
- **Day-rate / contract** — contract-to-permanent role; have rate thinking ready.
- **The "combo" claim** — this artifact is the evidence: point Yuriy at the plugin/transport/DSP
  (⏳), Nat at the server/accounts/hub/infra (✅ and growing).

---

## Latency budget (hop by hop) — fill in measured numbers once the meter lands

`host capture → encode (Opus) → uplink to Modal (UK↔UK, region-pinned) → inference (causal,
small frames, no lookahead) → downlink → decode → playout`. The **unavoidable** floor is the
physical round-trip; everything else (cold start, codec, NAT path) is attacked and then *measured*.
Honest cover line: *"You can't beat the speed of light; you can make everything else negligible and
show exactly what's left."*
