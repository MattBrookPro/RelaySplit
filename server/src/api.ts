import express from "express";
import path from "node:path";
import { mintTurnCredential } from "./turn";
import { listRooms } from "./presence";
import { createAccountsRouter } from "./accounts";

// REST surface of the control plane. Small on purpose: health, TURN minting, presence, and the
// static client. Everything latency-sensitive (the audio) deliberately lives elsewhere.
export function createApi(): express.Express {
  const app = express();
  app.use(express.json());

  // Liveness probe. Also the fastest way to confirm nginx -> :8080 is wired end to end
  // (a 502 here means this process isn't up; a 200 means the proxy path is healthy).
  app.get("/api/health", (_req, res) => {
    res.json({ status: "ok", service: "relaysplit-control", time: new Date().toISOString() });
  });

  // Ephemeral TURN credentials. WHY mint per call: each session gets its own short-lived
  // credential, so nothing long-lived is ever exposed client-side. (An auth gate is added in
  // the accounts slice; for now any caller may mint a 5-minute credential.)
  app.post("/api/turn", (req, res) => {
    const label = typeof req.body?.label === "string" ? req.body.label : "anon";
    res.json(mintTurnCredential(label));
  });

  // Presence snapshot for a control UI: which sessions are live and who's in them.
  app.get("/api/presence", (_req, res) => {
    res.json({ rooms: listRooms() });
  });

  // Live-broadcast presence. The GPU container REPORTS its live broadcasts here (with a short TTL)
  // while it is running; clients poll this VPS endpoint, never the GPU directly. This is the fix for a
  // costly bug: previously /api/live proxied to the container, so any open browser tab polling every few
  // seconds kept the scale-to-zero GPU permanently warm. Now an idle tab only ever touches the VPS, and
  // the GPU scales to zero whenever there is no active session reporting in.
  const liveBroadcasts = new Map<number, number>(); // channelId -> expiry (epoch ms)
  const BROADCAST_TTL_MS = 12_000;

  app.post("/api/broadcasts", (req, res) => {
    const channels: number[] = Array.isArray(req.body?.channels) ? req.body.channels.map(Number) : [];
    const until = Date.now() + BROADCAST_TTL_MS;
    for (const id of channels) if (Number.isFinite(id)) liveBroadcasts.set(id, until);
    res.json({ ok: true, count: channels.length });
  });

  app.get("/api/live", (_req, res) => {
    const now = Date.now();
    const broadcasts: { channel: number; live: boolean }[] = [];
    for (const [id, exp] of liveBroadcasts) {
      if (exp > now) broadcasts.push({ channel: id, live: true });
      else liveBroadcasts.delete(id); // expire stale entries (container stopped reporting)
    }
    res.json({ broadcasts });
  });

  // Accounts / peers / channels (SQLite-backed): /api/register, /login, /me, /peers, /channels.
  app.use("/api", createAccountsRouter());

  // Static client (landing/app). Kept dependency-free for now; a React client is a later slice.
  app.use(express.static(path.resolve(__dirname, "../public")));

  return app;
}
