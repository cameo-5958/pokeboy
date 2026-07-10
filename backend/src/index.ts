import cors from "cors";
import express from "express";

import { config, paths } from "./config.js";
import { cartridgesRouter } from "./routes/cartridges.js";
import { modsRouter } from "./routes/mods.js";

const app = express();

app.use(cors());
app.use(express.json());

// Static label images.
app.use("/labels", express.static(paths.labels()));

app.get("/health", (_req, res) => res.json({ ok: true }));

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
    res.status(500).json({ error: "Internal server error" });
  },
);

app.listen(config.port, () => {
  console.log(`pokeboy backend listening on http://localhost:${config.port}`);
  console.log(`storage: ${config.storageDir}`);
});
