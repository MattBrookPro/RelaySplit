import express from "express";
import db from "./db";
import { type Account, authFromHeader, createSession, hashPassword, verifyPassword } from "./auth";

// Account / peer / channel REST surface. Mounted under /api. This is the persistent control-plane
// data layer (the in-memory presence registry in presence.ts handles live/ephemeral state).
export function createAccountsRouter(): express.Router {
  const r = express.Router();

  const requireAuth = (req: express.Request, res: express.Response): Account | null => {
    const acct = authFromHeader(req.header("authorization"));
    if (!acct) {
      res.status(401).json({ error: "unauthorized" });
      return null;
    }
    return acct;
  };

  r.post("/register", (req, res) => {
    const { username, password } = req.body ?? {};
    if (typeof username !== "string" || typeof password !== "string" || username.length < 3 || password.length < 6) {
      return res.status(400).json({ error: "username (>=3) and password (>=6) required" });
    }
    if (db.prepare("SELECT id FROM accounts WHERE username = ?").get(username)) {
      return res.status(409).json({ error: "username taken" });
    }
    const info = db
      .prepare("INSERT INTO accounts (username, pw_hash, created) VALUES (?, ?, ?)")
      .run(username, hashPassword(password), new Date().toISOString());
    const id = Number(info.lastInsertRowid);
    res.json({ token: createSession(id), account: { id, username } });
  });

  r.post("/login", (req, res) => {
    const { username, password } = req.body ?? {};
    const row = db.prepare("SELECT id, username, pw_hash FROM accounts WHERE username = ?").get(username) as
      | { id: number; username: string; pw_hash: string }
      | undefined;
    if (!row || !verifyPassword(String(password ?? ""), row.pw_hash)) {
      return res.status(401).json({ error: "bad credentials" });
    }
    res.json({ token: createSession(row.id), account: { id: row.id, username: row.username } });
  });

  r.get("/me", (req, res) => {
    const acct = requireAuth(req, res);
    if (acct) res.json({ account: acct });
  });

  // Peers — bidirectional connections between accounts.
  r.get("/peers", (req, res) => {
    const acct = requireAuth(req, res);
    if (!acct) return;
    const peers = db
      .prepare("SELECT a.id, a.username FROM peers p JOIN accounts a ON a.id = p.peer_id WHERE p.account_id = ?")
      .all(acct.id);
    res.json({ peers });
  });
  r.post("/peers", (req, res) => {
    const acct = requireAuth(req, res);
    if (!acct) return;
    const other = db.prepare("SELECT id, username FROM accounts WHERE username = ?").get(req.body?.username) as
      | Account
      | undefined;
    if (!other) return res.status(404).json({ error: "no such account" });
    if (other.id === acct.id) return res.status(400).json({ error: "cannot peer with self" });
    const add = db.prepare("INSERT OR IGNORE INTO peers (account_id, peer_id) VALUES (?, ?)");
    add.run(acct.id, other.id);
    add.run(other.id, acct.id); // peering is mutual
    res.json({ ok: true, peer: other });
  });

  // Channels — a sender's channels (each carries a stem selection).
  r.get("/channels", (req, res) => {
    const acct = requireAuth(req, res);
    if (!acct) return;
    res.json({ channels: db.prepare("SELECT id, name, stem, created FROM channels WHERE owner_id = ?").all(acct.id) });
  });
  r.post("/channels", (req, res) => {
    const acct = requireAuth(req, res);
    if (!acct) return;
    const name = String(req.body?.name ?? "").trim() || "channel";
    const stem = String(req.body?.stem ?? "vocals");
    const info = db
      .prepare("INSERT INTO channels (owner_id, name, stem, created) VALUES (?, ?, ?, ?)")
      .run(acct.id, name, stem, new Date().toISOString());
    res.json({ channel: { id: Number(info.lastInsertRowid), name, stem } });
  });

  return r;
}
