import { Router } from "express";

import { config } from "../config.js";
import { generateKey, listKeys, maskKey, revokeKey } from "../keys.js";

export const keysRouter = Router();

// Admin guard: HTTP key management requires ADMIN_TOKEN to be configured and a
// matching `x-admin-token` header. Without ADMIN_TOKEN the endpoints are off
// (mint locally with `npm run key:new` instead).
keysRouter.use((req, res, next) => {
  if (!config.adminToken) {
    return res.status(403).json({ error: "Key management over HTTP is disabled (set ADMIN_TOKEN)" });
  }
  if (req.get("x-admin-token") !== config.adminToken) {
    return res.status(401).json({ error: "Invalid or missing admin token" });
  }
  next();
});

// Mint a new device key. The full secret is returned once, here.
keysRouter.post("/", async (req, res, next) => {
  try {
    const label = typeof req.body?.label === "string" && req.body.label.trim()
      ? req.body.label.trim()
      : "device";
    const record = await generateKey(label);
    res.status(201).json(record);
  } catch (e) {
    next(e);
  }
});

// List issued keys (masked - the full secret is never re-shown).
keysRouter.get("/", async (_req, res, next) => {
  try {
    const keys = await listKeys();
    res.json(keys.map((k) => ({ key: maskKey(k.key), label: k.label, createdAt: k.createdAt })));
  } catch (e) {
    next(e);
  }
});

// Revoke a key by its full value.
keysRouter.delete("/:key", async (req, res, next) => {
  try {
    const removed = await revokeKey(req.params.key);
    if (!removed) return res.status(404).json({ error: "Key not found" });
    res.json({ ok: true });
  } catch (e) {
    next(e);
  }
});
