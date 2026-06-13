// Signalling smoke test: proves the /ws relay actually carries a message between two peers in
// the same room. Two clients connect, join the same room, and one sends a `signal` addressed to
// the other; the test passes only if that exact payload is relayed through and arrives.
//
// WHY a test for "it just forwards JSON": the relay IS the control plane — if join/relay/presence
// regress, every WebRTC session silently fails to negotiate. This catches that in 8 seconds.
//
//   node server/scripts/ws-smoke.cjs                # hits the live VPS over wss
//   WS_URL=ws://localhost:8080/ws node ws-smoke.cjs # hits a local dev server
const WebSocket = require("ws");

const URL = process.env.WS_URL || "wss://relaysplit.vaguelystrange.com/ws";
const room = "smoke-" + Date.now();
const a = new WebSocket(URL);
const b = new WebSocket(URL);

let aJoined = false;
let bId = null;
const fail = (msg) => { console.error("FAIL:", msg); process.exit(1); };
const timer = setTimeout(() => fail("timeout — no relayed signal within 8s"), 8000);

a.on("message", (raw) => {
  const m = JSON.parse(raw);
  if (m.type === "welcome") a.send(JSON.stringify({ type: "join", room, role: "host", name: "A" }));
  if (m.type === "joined") aJoined = true;
  // B arrives -> A fires a signal at it.
  if (m.type === "peer-joined") {
    bId = m.peerId;
    a.send(JSON.stringify({ type: "signal", to: bId, data: { hello: "world" } }));
  }
});

b.on("message", (raw) => {
  const m = JSON.parse(raw);
  // Only join once A is already in the room, so A reliably gets a peer-joined for B.
  if (m.type === "welcome") {
    const join = () => (aJoined ? b.send(JSON.stringify({ type: "join", room, role: "receiver", name: "B" })) : setTimeout(join, 50));
    join();
  }
  if (m.type === "signal") {
    if (m.data && m.data.hello === "world") {
      console.log("PASS: signal relayed from", m.from);
      clearTimeout(timer);
      a.close(); b.close();
      process.exit(0);
    }
    fail("relayed payload did not match");
  }
});

a.on("error", (e) => fail("A socket error: " + e.message));
b.on("error", (e) => fail("B socket error: " + e.message));
