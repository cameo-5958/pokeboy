import { Router } from "express";

import { drainCommands, submitResult } from "../dev.js";

export const devRouter = Router();

// Device poll: hands over (and clears) the queued dev commands.
devRouter.get("/commands", (req, res) => {
  const device = typeof req.query.device === "string" ? req.query.device : "";
  if (!device) return res.status(400).json({ error: "device query param required" });
  res.json({ commands: drainCommands(device) });
});

// Device posts a command's outcome (ack for presses, PNG for screenshots).
devRouter.post("/result", (req, res) => {
  const body = req.body as { id?: unknown; ok?: unknown; error?: unknown; png?: unknown };
  if (typeof body?.id !== "string") return res.status(400).json({ error: "id required" });
  const accepted = submitResult({
    id: body.id,
    ok: body.ok !== false,
    error: typeof body.error === "string" ? body.error : undefined,
    png: typeof body.png === "string" ? body.png : undefined,
  });
  res.json({ accepted });
});
