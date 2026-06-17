// pm2 process definition for the RelaySplit control plane.
//
// WHY tsx instead of a compiled build: it lets the VPS run the TypeScript entry directly, so
// the iteration loop is just `git pull && pm2 restart relaysplit` with no build step. For a
// hardened production artifact, `npm run build` emits dist/ and you'd point script at
// dist/index.js — left as a deliberate later step, noted so it's a conscious trade-off.
module.exports = {
  apps: [
    {
      name: "relaysplit",
      cwd: __dirname, // server/ — where node_modules/.bin/tsx lives
      script: "node_modules/.bin/tsx",
      args: "src/index.ts",
      env: { NODE_ENV: "production" },
      max_restarts: 10,
      restart_delay: 2000,
    },
  ],
};
