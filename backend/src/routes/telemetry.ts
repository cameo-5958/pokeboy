import { Router } from "express";

import { getSession, ingest, listSessions } from "../telemetry.js";

export const telemetryRouter = Router();

telemetryRouter.post("/", (req, res) => {
  try {
    ingest(req.body);
    res.sendStatus(204);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid telemetry batch";
    res.status(400).json({ error: message });
  }
});

telemetryRouter.get("/", (_req, res) => {
  res.json(listSessions());
});

telemetryRouter.get("/:key", (req, res) => {
  const session = getSession(req.params.key);
  if (!session) return res.status(404).json({ error: "Telemetry session not found" });
  res.json(session);
});
