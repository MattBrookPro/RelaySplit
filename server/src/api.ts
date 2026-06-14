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

  // Accounts / peers / channels (SQLite-backed): /api/register, /login, /me, /peers, /channels.
  app.use("/api", createAccountsRouter());

  // Static client (landing/app). Kept dependency-free for now; a React client is a later slice.
  app.use(express.static(path.resolve(__dirname, "../public")));

  return app;
}
