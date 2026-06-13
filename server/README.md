# RelaySplit control server

The **control plane** for RelaySplit: WebRTC signalling, presence/channels, and ephemeral TURN
credential minting. **Audio never flows through this process** — it takes the data plane
(WebRTC, peer-to-peer or TURN-relayed). That separation is the project's core architecture.

## Surface

| Path | What |
|------|------|
| `GET /api/health` | Liveness probe (also confirms nginx → :8080 is wired). |
| `POST /api/turn` | Mint a short-lived ephemeral TURN credential (coturn `use-auth-secret`). |
| `GET /api/channels` | Presence snapshot — live sessions and their peers. |
| `WS /ws` | WebRTC signalling relay (join a room, relay SDP/ICE, presence events). |
| `GET /` | Minimal static landing/app page. |

## Stack & deviations

- **TypeScript + Express + `ws`**, run via **tsx** under **pm2** (fast `git pull && pm2 restart`
  loop; `npm run build` emits a compiled `dist/` for production hardening).
- **nginx + certbot, not Caddy.** The brief specifies Caddy, but the VPS already runs
  nginx+certbot with a valid cert proxying `/ → localhost:8080` (incl. WS upgrade headers).
  Replacing a working TLS proxy would be reckless; we keep nginx. The server is proxy-agnostic.

## Run locally

```bash
cd server
npm install
cp .env.example .env   # fill TURN_SECRET to actually mint working credentials
npm run dev            # tsx watch on :8080
```

## Deploy (VPS, under pm2 behind nginx)

The repo is cloned at `/root/apps/relaysplit` (read-only deploy key). `.env` with the real
`TURN_SECRET` lives at that repo root and is git-ignored.

```bash
cd /root/apps/relaysplit && git pull
cd server && npm install
pm2 startOrReload ecosystem.config.cjs   # first time: pm2 start ecosystem.config.cjs
pm2 save
```

Verify: `curl -s https://relaysplit.vaguelystrange.com/api/health`.
