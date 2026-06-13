import crypto from "node:crypto";
import { config } from "./config";

// Ephemeral TURN credentials — coturn's `use-auth-secret` ("TURN REST API") scheme.
//
// WHY this exists: TURN relay time costs money and bandwidth, so the relay must authenticate
// clients. Handing the long-term shared secret to every browser/plugin would let anyone mint
// unlimited relay time forever. Instead we derive a SHORT-LIVED credential from the secret,
// server-side, per session:
//
//     username    = "<unix-expiry>:<label>"
//     credential  = base64( HMAC_SHA1( shared_secret, username ) )
//
// coturn recomputes the same HMAC with its copy of the secret and rejects any username whose
// timestamp has passed. So a leaked credential is worthless within minutes, and the secret
// itself never leaves the server. This is the same scheme we validated by hand on the VPS
// before writing a line of server code.
export interface TurnCredential {
  username: string;
  credential: string;
  ttl: number;
  expiresAt: number;
  iceServers: Array<{ urls: string[]; username?: string; credential?: string }>;
}

export function mintTurnCredential(label: string, ttlSeconds = 300): TurnCredential {
  const expiresAt = Math.floor(Date.now() / 1000) + ttlSeconds;
  const username = `${expiresAt}:${label}`;
  const credential = crypto
    .createHmac("sha1", config.turnSecret)
    .update(username)
    .digest("base64");

  return {
    username,
    credential,
    ttl: ttlSeconds,
    expiresAt,
    // Returned ready-to-use as an RTCConfiguration.iceServers array. STUN first (cheap, direct
    // when possible); TURN over UDP then TURNS over TLS as relay fallbacks for strict NATs.
    iceServers: [
      { urls: ["stun:stun.l.google.com:19302"] },
      { urls: [`turn:${config.turnHost}:3478?transport=udp`], username, credential },
      { urls: [`turns:${config.turnHost}:5349?transport=tcp`], username, credential },
    ],
  };
}
