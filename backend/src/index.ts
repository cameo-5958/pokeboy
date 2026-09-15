import cors from "cors";
import express from "express";

import { config, paths } from "./config.js";
import { hasKeys, isValidKey } from "./keys.js";
import { startMcpServer } from "./mcp.js";
import { pruneOrphans, watchRegistryForPrune } from "./prune.js";
import { cartridgesRouter } from "./routes/cartridges.js";
import { devRouter } from "./routes/dev.js";
import { keysRouter } from "./routes/keys.js";
import { modsRouter } from "./routes/mods.js";
import { registryRouter } from "./routes/registry.js";
import { telemetryRouter } from "./routes/telemetry.js";
import { battleLinkRouter, initBattleLinkDiscord } from "./routes/battle-link.js";
import { webRouter } from "./routes/web.js";
import { hasWebSession } from "./websession.js";

const app = express();

// nginx terminates TLS on this host and proxies over loopback. Trusting the
// loopback proxy makes req.protocol honor X-Forwarded-Proto, so absolute URLs
// built from requests (label images, ROM links) come out https:// instead of
// http:// — which iOS ATS refuses to fetch. Direct (non-proxied) requests,
// e.g. via the tailnet IP, are unaffected.
app.set("trust proxy", "loopback");

app.use(cors());

// Browser player vhost: emulator.cameo.moe serves the web shell at its root.
// API/labels/battle-link/health paths pass through untouched so the shell's
// same-origin fetches keep working.
app.use((req, _res, next) => {
  if (
    req.hostname === config.webHost &&
    !req.path.startsWith("/api") &&
    !req.path.startsWith("/labels") &&
    !req.path.startsWith("/battle-link") &&
    !req.path.startsWith("/health") &&
    !req.path.startsWith("/web")
  ) {
    req.url = "/web" + req.url;
  }
  next();
});

// Mounted before the app-level express.json: save-state uploads exceed the
// global 1mb body limit, so the web router parses its own bodies.
app.use("/web", webRouter);
// 1mb: dev-mode screenshot results carry a base64 PNG of the 160x144 LCD.
app.use(express.json({ limit: "1mb" }));

// Static label images.
app.use("/labels", express.static(paths.labels()));
app.get("/health", (_req, res) => res.json({ ok: true }));
// Public by design: the ROM long-polls this endpoint and the browser console
// submits trainer commands without sharing the Pokeboy device API key.
app.use("/battle-link", battleLinkRouter);

// Key management. Mounted before the data-key guard so a fresh device can be
// issued its first key. Guards itself with ADMIN_TOKEN.
app.use("/api/keys", keysRouter);

// Data-key guard. Auth activates automatically once any key has been issued
// (or the legacy API_KEY env is set); until then the API is open for local dev.
app.use("/api", async (req, res, next) => {
  try {
    const authActive = config.apiKey || (await hasKeys());
    if (!authActive) return next();
    if (hasWebSession(req)) return next(); // signed-in browser player
    if (await isValidKey(req.get("x-api-key"))) return next();
    return res.status(401).json({ error: "Invalid or missing API key" });
  } catch (e) {
    next(e);
  }
});

app.use("/api/registry", registryRouter);
app.use("/api/cartridges", cartridgesRouter);
app.use("/api/mods", modsRouter);
app.use("/api/telemetry", telemetryRouter);
app.use("/api/dev", devRouter);

// Centralized error handler.
app.use(
  (
    err: unknown,
    _req: express.Request,
    res: express.Response,
    _next: express.NextFunction,
  ) => {
    console.error(err);
    // Honor a status set by upstream middleware (e.g. body-parser's 400 on
    // malformed JSON); otherwise treat it as an internal error.
    const status = (err as { statusCode?: number; status?: number }).statusCode
      ?? (err as { status?: number }).status
      ?? 500;
    const message = status === 500 ? "Internal server error" : (err as Error).message;
    res.status(status).json({ error: message });
  },
);

async function start(): Promise<void> {
  await pruneOrphans();
  watchRegistryForPrune();
  initBattleLinkDiscord();

  app.listen(config.port, () => {
    console.log(`pokeboy backend listening on http://localhost:${config.port}`);
    console.log(`roms:   ${config.romsDir}`);
    console.log(`mods:   ${config.modsDir}`);
  });

  startMcpServer();
}

void start().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
