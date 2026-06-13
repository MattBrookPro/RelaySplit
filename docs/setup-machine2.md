# Setup — Machine 2: Local Windows Workstation (Dev + Modal CLI)

**Role:** development machine and Modal CLI driver; later, the JUCE plugin host.
This machine **drives Modal** and **does not run the Node server** — that lives on the VPS.

As-built record. Verified values are marked ✅; deferred items are marked ⏳.

---

## Step 1 — Modal CLI auth ✅

Modal is authenticated on this machine.

> ⚠️ The guide's `modal token list` is **stale** — the installed CLI (**v1.5.0**) has no
> `list` subcommand. Use `modal token info` instead.

```
modal token info        # shows the token currently in use
modal app list          # confirms the API is reachable end-to-end
```

Confirmed:

- Modal client version: **1.5.0**
- Workspace: **`blitzncs`**, User: **BlitzNCS**
- `modal app list` reaches the API (many existing deployed apps).

(Note: the Modal workspace is `blitzncs`; the GitHub account for this repo is `MattBrookPro`.
Different namespaces — that's expected and fine.)

---

## Step 2 — UK/EU GPU region (verified live) ✅

The region string is the one value worth getting exactly right — closer datacenter =
smaller network hop, which is the whole latency thesis.

### How the region was chosen (empirical, not assumed)

The VPS hostname was resolved and its IP geolocated:

- `relaysplit.vaguelystrange.com` → **`204.168.135.201`**
- Geolocation: **Hetzner Online GmbH (AS24940), Helsinki, Finland** — Northern Europe.

So the GPU should sit in **Northern Europe** to minimise the GPU→VPS hop.

### Decision

```python
@app.function(gpu="...", region="eu-north")   # primary pin — closest to a Helsinki VPS
```

- **Primary:** `region="eu-north"` (narrow — Northern Europe, closest to Helsinki).
- **Fallback:** `region="eu"` (broad — European Economic Area) if `eu-north` lacks the
  desired GPU at deploy time, or to avoid the higher narrow-region cost multiplier.

> `uk` and `eu-west` were the natural first guesses but are **farther** from Finland than
> `eu-north`. Don't pin those.

### Verified region identifiers (from https://modal.com/docs/guide/region-selection)

Parameter is `region=` on `@app.function`. Full documented set:

| Broad | Narrow | Notes |
|-------|--------|-------|
| `us`  | `us-east`, `us-central`, `us-south`, `us-west` | United States |
| `eu`  | `eu-west`, `eu-north`, `eu-south` | European Economic Area |
| `ap`  | `ap-northeast`, `ap-southeast`, `ap-south`, `ap-melbourne`, `jp`, `au` | Asia-Pacific |
| `uk`  | — | United Kingdom |
| `ca`  | — | Canada |
| `me`  | — | Middle East |
| `sa`  | — | South America |
| `af`  | — | Africa |
| `mx`  | — | Mexico |

### Cost & GPU availability caveats

- **Cost multiplier:** pinning a region applies a multiplier on top of base pricing
  (the docs page indicated a higher multiplier for *narrow* regions than *broad* ones; a
  separate summary quoted different geography-based figures). **Re-confirm the exact current
  multiplier on https://modal.com/pricing before relying on cost numbers.** `eu-north` is a
  narrow region, so it costs more than broad `eu`.
- **GPU availability per region is not published.** Platform GPU types are L4, A10,
  A100 (40/80 GB), L40S, H100, H200, B200 — but not every type is in every region. Modal
  errors at deploy/run if the chosen GPU isn't available in the pinned region. **Plan:** pin
  `eu-north`, deploy, and if scheduling fails, either switch the GPU type or fall back to
  broad `eu`.

---

## Step 3 — WebRTC-capable browser + ICE test page ✅

Installed and confirmed on this machine:

- **Google Chrome** — `C:\Program Files\Google\Chrome\Application\chrome.exe`
- **Microsoft Edge 149** — `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`

Both are fully WebRTC-capable.

**Trickle ICE test page to bookmark:**
https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/

Use it later to confirm the VPS TURN server hands out `relay` candidates once the signalling
server can mint ephemeral TURN credentials. Remember the TURN relay UDP range is constrained
(see VPS facts below) — `relay` candidates will fall inside **49152–50999/udp**.

---

## Step 4 — JUCE + CMake (Phase 4, deferred) ⏳

Not needed until the plugin build, **after** the WebRTC→Modal spike succeeds. On the list,
but must not block earlier phases:

- JUCE (Projucer or CMake subdirectory)
- CMake
- C++ toolchain — MSVC via Visual Studio Build Tools
- First validate a trivial "hello" JUCE plugin builds and loads in a DAW before the real one.

---

## Where the Node server runs: the VPS, not here

The signalling/control server runs **directly on the VPS** — do not run it locally.

- No Node needed on this Windows machine for the server (only later for the React client
  build, which can also be built on the VPS).
- Single app on `:8080`: `/ws` signalling WebSocket, `/api/*` control REST (accounts, peers,
  presence, ephemeral TURN credential minting), `/login` + `/app` UI.
- Run under **pm2**; **nginx + certbot** (not Caddy) proxies all paths to `localhost:8080`.
- Iterate by editing in the repo → push → `git pull` + pm2 restart on the VPS (or edit
  directly over SSH for fast spikes).
- "Test it locally first" does **not** apply to the server — it's tested where it lives, on
  the VPS, against the real nginx + TURN + cert.

### VPS facts to build against (as-built)

- Control plane: **nginx + certbot**, proxying all paths → `localhost:8080`.
- TURN relay UDP range: **49152–50999/udp** (capped below WireGuard's 51820). Client ICE
  config must **not** assume the wider default range.
- TURN secret for minting ephemeral credentials: **`/root/apps/relaysplit/.env`** on the VPS.

---

## Build order this machine drives

1. **Write + deploy the Modal script** (the gate — no script, no container URL).
2. **Spike WebRTC → Modal** in isolation (the one genuinely uncertain part).
3. **Node signalling/control server, on the VPS.**
4. **Browser ↔ browser** WebRTC through VPS signalling + TURN.
5. **Browser ↔ Modal** WebRTC (the moment of truth — needs the deployed container URL).
6. **JUCE plugin** as WebRTC client + latency meter, then accounts/channels/hub/receiver.

---

## Done when

- [x] `modal token info` confirms auth; `modal` commands reach the API.
- [x] Confirmed EU region string written down: **`eu-north`** (fallback `eu`), chosen from
      the VPS being in Helsinki/Finland.
- [x] A WebRTC-capable browser (Chrome + Edge) and the Trickle ICE test page are ready.
- [ ] (Deferred) JUCE/CMake noted for the plugin phase.
- [x] Understood: the Node server lives and runs on the VPS, not here.
