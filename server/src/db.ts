import path from "node:path";
import Database from "better-sqlite3";

// SQLite control-plane store. WHY SQLite: the control plane is low-write and single-node — a file
// DB is the right tool (transactional, zero ops, trivial backup). It lives at the repo ROOT on the
// VPS (next to .env), so it survives code redeploys, which only replace the server/ folder.
const db = new Database(path.resolve(__dirname, "../../relaysplit.db"));
db.pragma("journal_mode = WAL");

db.exec(`
  CREATE TABLE IF NOT EXISTS accounts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    pw_hash  TEXT NOT NULL,
    created  TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    expires    INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS peers (
    account_id INTEGER NOT NULL,
    peer_id    INTEGER NOT NULL,
    PRIMARY KEY (account_id, peer_id)
  );
  CREATE TABLE IF NOT EXISTS channels (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    name     TEXT NOT NULL,
    stem     TEXT NOT NULL DEFAULT 'vocals',
    created  TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS shares (
    channel_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    PRIMARY KEY (channel_id, account_id)
  );
`);

export default db;
