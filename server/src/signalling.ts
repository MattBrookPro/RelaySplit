import { WebSocketServer, WebSocket } from "ws";
import type { Server } from "node:http";
import { randomUUID } from "node:crypto";
import { addPeer, removePeer, roomPeers, getPeer, type Role } from "./presence";
import { accountForToken } from "./auth";

// WebRTC signalling over WebSocket — the heart of the CONTROL plane.
//
// WHY a relay rather than a peer: before media can flow, the two endpoints must exchange SDP
// offers/answers and ICE candidates — but they can't talk directly yet (that's exactly what
// they're negotiating). The server is a dumb, fast courier for that signalling JSON and never
// touches the audio. The audio is the DATA plane: it goes peer-to-peer, or TURN-relayed, but
// never through this process. That control/data split is the project's core architecture.

interface InboundJoin {
  type: "join";
  room: string;
  role?: Role;
  name?: string;
  token?: string; // optional session token -> ties this peer to an account
}
interface InboundSignal {
  type: "signal";
  to: string;
  data: unknown; // opaque SDP / ICE payload — the server never inspects it
}
interface InboundLeave {
  type: "leave";
}
type Inbound = InboundJoin | InboundSignal | InboundLeave;

function send(ws: WebSocket, msg: unknown): void {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

export function attachSignalling(server: Server): WebSocketServer {
  // Mounted on /ws so REST (/api/*) and static files share one origin and port behind nginx,
  // whose config already forwards the Upgrade/Connection headers this needs.
  const wss = new WebSocketServer({ server, path: "/ws" });

  wss.on("connection", (ws) => {
    // The SERVER assigns identity so a client can't spoof another peer's id in its messages.
    const peerId = randomUUID();
    let room: string | null = null;
    let role: Role = "receiver";

    send(ws, { type: "welcome", peerId });

    ws.on("message", (raw) => {
      let msg: Inbound;
      try {
        msg = JSON.parse(raw.toString());
      } catch {
        return; // ignore malformed frames rather than crash the socket
      }

      if (msg.type === "join") {
        room = msg.room;
        role = msg.role ?? "receiver";
        // If a session token is supplied, tag the peer with its account username (identity/presence).
        const name = accountForToken(msg.token)?.username ?? msg.name;
        const others = addPeer({ id: peerId, role, room, socket: ws, name });
        // Tell the joiner who's already here (whom to offer to)...
        send(ws, {
          type: "joined",
          room,
          peerId,
          peers: others.map((p) => ({ id: p.id, role: p.role, name: p.name })),
        });
        // ...and tell the others that someone arrived.
        for (const p of others) {
          send(p.socket, { type: "peer-joined", peerId, role, name });
        }
        return;
      }

      if (msg.type === "signal" && room) {
        // Pure relay: forward the opaque SDP/ICE blob to the named peer, stamped with the
        // sender's id so the recipient knows who it's negotiating with.
        const target = getPeer(room, msg.to);
        if (target) send(target.socket, { type: "signal", from: peerId, data: msg.data });
        return;
      }

      if (msg.type === "leave") {
        ws.close();
      }
    });

    ws.on("close", () => {
      if (!room) return;
      removePeer(room, peerId);
      // Let the rest of the room tear down their peer connection to this id.
      for (const p of roomPeers(room)) send(p.socket, { type: "peer-left", peerId });
    });
  });

  return wss;
}
