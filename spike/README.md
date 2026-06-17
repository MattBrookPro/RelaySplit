# Step 0 spike — WebRTC audio into/out of a Modal container

Proves the one uncertain thing before anything is built around it: can an
`aiortc` peer **inside a serverless Modal container** receive an audio track and
send one back, reachable from a browser, with media relayed through the VPS TURN
server? No model, no GPU — just a passthrough echo.

## What it does

- `relaysplit_spike.py` deploys a CPU-only Modal container (`region="uk"`) running
  a tiny FastAPI app (`@modal.asgi_app()`):
  - `GET /` — a self-contained browser test page (mic capture → WebRTC → playback).
  - `GET /ice` — returns the ICE config (Google STUN + the VPS TURN server).
  - `POST /offer` — an `aiortc` peer takes the browser's SDP offer, echoes the
    inbound audio back via `MediaRelay`, and returns the SDP answer.
- Signalling is **self-contained** because the VPS Node signalling server isn't up
  yet (nginx 502 on `:8080`). When it exists, only the SDP/ICE exchange location changes.

## Why TURN (and why it's a Modal Secret, not in this repo)

A serverless Modal container has no public inbound address, so WebRTC media is
relayed through the VPS TURN server (`relaysplit.vaguelystrange.com`, coturn,
relay range 49152–50999/udp). The container and the browser both connect *outbound*
to TURN.

TURN uses **ephemeral credentials** minted from the secret in
`/root/apps/relaysplit/.env` on the VPS (coturn `use-auth-secret`). The spike's
credential lives in a Modal Secret, never in git:

```
modal secret create relaysplit-turn-spike \
  TURN_HOST=relaysplit.vaguelystrange.com \
  TURN_REALM=relaysplit.vaguelystrange.com \
  TURN_USERNAME=<expiry-unix-ts> \
  TURN_CREDENTIAL=<base64 hmac-sha1(secret, username)> \
  --force
```

**Re-mint when it expires** (24h TTL). On the VPS:

```bash
S=$(grep -E '^TURN_SECRET=' /root/apps/relaysplit/.env | cut -d= -f2-)
E=$(( $(date +%s) + 86400 )); U="$E"
P=$(printf '%s' "$U" | openssl dgst -sha1 -hmac "$S" -binary | openssl base64)
echo "username=$U  credential=$P"
```

Then re-run the `modal secret create ... --force` above with the new values.

## Run it

```bash
modal deploy spike/relaysplit_spike.py     # persistent URL
# or
modal serve  spike/relaysplit_spike.py     # hot-reload during dev
```

Open the printed `*.modal.run` URL in Chrome/Edge, **put headphones on**, click
**Start**, allow the mic, and speak.

## Success / what to read

- **Conn: `connected`** and you hear your own voice echoed back → ✅ the spike passes:
  WebRTC media round-trips through a Modal container.
- **Path** badge shows the selected ICE candidate pair, e.g.:
  - `relay ↔ relay` / `relay ↔ srflx` → media is going through the VPS TURN relay (expected).
  - `srflx ↔ srflx` → Modal's egress NAT allowed a direct hole-punch (bonus; lower latency).
- **Conn: `failed`** → connectivity didn't establish. Check: TURN cred not expired,
  VPS firewall open on 3478/udp + 49152–50999/udp, container can egress UDP.

## If the spike fails

Per the runbook, fall back to a **WebSocket-PCM** path into the container instead of
WebRTC-into-Modal (the final hop to the user stays WebRTC regardless). Cheaper to
learn here than three phases deep.
