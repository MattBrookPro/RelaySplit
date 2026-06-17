import path from "node:path";
import dotenv from "dotenv";

// Load the VPS secrets file that holds the coturn shared secret. It lives at the repo root
// (/root/apps/relaysplit/.env), two levels up from server/src. We resolve it relative to THIS
// file rather than process.cwd() because pm2's working directory is easy to get wrong, and a
// silently-unloaded .env would mean every minted TURN credential is invalid.
dotenv.config({ path: path.resolve(__dirname, "../../.env") });

// Fail fast: a control server that boots without the TURN secret would hand out broken
// credentials and only fail much later, at connection time, where it's far harder to diagnose.
function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `Missing required env var ${name}. Set it in /root/apps/relaysplit/.env (see server/.env.example).`,
    );
  }
  return value;
}

export const config = {
  port: Number(process.env.SIGNAL_PORT ?? 8080),
  turnSecret: required("TURN_SECRET"),
  turnRealm: process.env.TURN_REALM ?? "relaysplit.vaguelystrange.com",
  turnHost: process.env.TURN_HOST ?? "relaysplit.vaguelystrange.com",
  // The GPU container, used to report which broadcasts are live (proxied so the browser app doesn't
  // make a cross-origin call to *.modal.run).
  liveUrl: process.env.LIVE_URL ?? "https://blitzncs--relaysplit-live-web.modal.run",
};
