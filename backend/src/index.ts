import cors from "cors";
import express from "express";

import { config, paths } from "./config.js";
import { hasKeys, isValidKey } from "./keys.js";
import { cartridgesRouter } from "./routes/cartridges.js";
import { keysRouter } from "./routes/keys.js";
import { modsRouter } from "./routes/mods.js";
import { registryRouter } from "./routes/registry.js";

const app = express();

app.use(cors());
app.use(express.json());

// Static label images.
app.use("/labels", express.static(paths.labels()));

app.get("/health", (_req, res) => res.json({ ok: true }));

// Key management. Mounted before the data-key guard so a fresh device can be
// issued its first key. Guards itself with ADMIN_TOKEN.
app.use("/api/keys", keysRouter);

// Data-key guard. Auth activates automatically once any key has been issued
// (or the legacy API_KEY env is set); until then the API is open for local dev.
app.use("/api", async (req, res, next) => {
  try {
    const authActive = config.apiKey || (await hasKeys());
    if (!authActive) return next();
    if (await isValidKey(req.get("x-api-key"))) return next();
    return res.status(401).json({ error: "Invalid or missing API key" });
  } catch (e) {
    next(e);
  }
});

app.use("/api/registry", registryRouter);
app.use("/api/cartridges", cartridgesRouter);
app.use("/api/mods", modsRouter);

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

app.listen(config.port, () => {
  console.log(`pokeboy backend listening on http://localhost:${config.port}`);
  console.log(`roms:   ${config.romsDir}`);
  console.log(`mods:   ${config.modsDir}`);
});
