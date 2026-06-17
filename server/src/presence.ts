import type { WebSocket } from "ws";

// In-memory registry of the control plane: which peers are connected and which room (session)
// each is in. WHY in-memory for now: the demo spine needs live signalling routing and presence,
// not durability. Persistent accounts/channels move to SQLite in a later slice (brief step 8);
// keeping that behind this small API means the migration stays local to this file.

export type Role = "host" | "modal" | "receiver";

export interface Peer {
  id: string;
  role: Role;
  room: string;
  socket: WebSocket;
  name?: string;
}

const rooms = new Map<string, Map<string, Peer>>();

// Add a peer to its room and return the peers ALREADY present, so the joiner knows whom to
// start offering to (the newcomer initiates; existing peers just wait for the offer).
export function addPeer(peer: Peer): Peer[] {
  let room = rooms.get(peer.room);
  if (!room) {
    room = new Map();
    rooms.set(peer.room, room);
  }
  const others = [...room.values()];
  room.set(peer.id, peer);
  return others;
}

export function removePeer(roomId: string, peerId: string): void {
  const room = rooms.get(roomId);
  if (!room) return;
  room.delete(peerId);
  if (room.size === 0) rooms.delete(roomId); // don't leak empty rooms
}

export function getPeer(roomId: string, peerId: string): Peer | undefined {
  return rooms.get(roomId)?.get(peerId);
}

export function roomPeers(roomId: string): Peer[] {
  return [...(rooms.get(roomId)?.values() ?? [])];
}

// Presence snapshot for the control UI (sockets stripped — this is safe to serialise to JSON).
export function listRooms() {
  return [...rooms.entries()].map(([id, peers]) => ({
    room: id,
    peers: [...peers.values()].map((p) => ({ id: p.id, role: p.role, name: p.name })),
  }));
}
