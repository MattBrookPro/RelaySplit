import crypto from "node:crypto";
import db from "./db";

export interface Account {
  id: number;
  username: string;
}

// Passwords: scrypt with a per-user random salt. WHY scrypt: memory-hard, in the Node stdlib (no
// dependency), and the right default for password storage. Stored as "salt:hash".
export function hashPassword(pw: string): string {
  const salt = crypto.randomBytes(16).toString("hex");
  const hash = crypto.scryptSync(pw, salt, 64).toString("hex");
  return `${salt}:${hash}`;
}

export function verifyPassword(pw: string, stored: string): boolean {
  const [salt, hash] = stored.split(":");
  if (!salt || !hash) return false;
  const test = crypto.scryptSync(pw, salt, 64).toString("hex");
  const a = Buffer.from(hash, "hex");
  const b = Buffer.from(test, "hex");
  return a.length === b.length && crypto.timingSafeEqual(a, b); // constant-time compare
}

// Sessions: opaque random bearer tokens with an expiry, stored server-side (revocable, unlike a
// stateless JWT). The same model later mints ephemeral TURN creds per session.
export function createSession(accountId: number, ttlSeconds = 86400): string {
  const token = crypto.randomBytes(24).toString("base64url");
  const expires = Math.floor(Date.now() / 1000) + ttlSeconds;
  db.prepare("INSERT INTO sessions (token, account_id, expires) VALUES (?, ?, ?)").run(token, accountId, expires);
  return token;
}

export function accountForToken(token?: string | null): Account | null {
  if (!token) return null;
  const row = db.prepare("SELECT account_id, expires FROM sessions WHERE token = ?").get(token) as
    | { account_id: number; expires: number }
    | undefined;
  if (!row || row.expires < Date.now() / 1000) return null;
  return (db.prepare("SELECT id, username FROM accounts WHERE id = ?").get(row.account_id) as Account | undefined) ?? null;
}

// Resolve the account from an Authorization header ("Bearer <token>" or a bare token).
export function authFromHeader(header?: string): Account | null {
  if (!header) return null;
  return accountForToken(header.startsWith("Bearer ") ? header.slice(7) : header);
}
