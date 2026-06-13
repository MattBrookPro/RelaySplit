// RelaySplit control plane — composition root.
//
// One HTTP server hosts BOTH the REST API (Express) and the WebRTC signalling (ws), sharing a
// single port (8080) that nginx terminates TLS in front of. Audio never flows through this
// process: it is the control plane only (signalling, presence, TURN minting). Keeping data off
// this path is what lets the audio take the shortest possible route independently.
import { createServer } from "node:http";
import { config } from "./config";
import { createApi } from "./api";
import { attachSignalling } from "./signalling";

const app = createApi();
const server = createServer(app);
attachSignalling(server);

server.listen(config.port, () => {
  console.log(`[relaysplit] control plane listening on :${config.port} (TURN realm ${config.turnRealm})`);
});
