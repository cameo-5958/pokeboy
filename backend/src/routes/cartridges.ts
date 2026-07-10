import { existsSync } from "node:fs";
import { Router } from "express";

import { findCartridge, readCartridges, romPath, type CartridgeRecord } from "../storage.js";

export const cartridgesRouter = Router();

/** Shapes a stored record into the API response the app expects. */
function toDto(c: CartridgeRecord, origin: string) {
  return {
    id: c.id,
    title: c.title,
    file: c.file,
    img: c.img ? `${origin}/labels/${c.img}` : null,
  };
}

cartridgesRouter.get("/", async (req, res, next) => {
  try {
    const origin = `${req.protocol}://${req.get("host")}`;
    const carts = await readCartridges();
    res.json(carts.map((c) => toDto(c, origin)));
  } catch (e) {
    next(e);
  }
});

cartridgesRouter.get("/:id", async (req, res, next) => {
  try {
    const cart = await findCartridge(req.params.id);
    if (!cart) return res.status(404).json({ error: "Cartridge not found" });
    const origin = `${req.protocol}://${req.get("host")}`;
    res.json(toDto(cart, origin));
  } catch (e) {
    next(e);
  }
});

// Streams the raw ROM bytes.
cartridgesRouter.get("/:id/rom", async (req, res, next) => {
  try {
    const cart = await findCartridge(req.params.id);
    if (!cart) return res.status(404).json({ error: "Cartridge not found" });
    const file = romPath(cart);
    if (!existsSync(file)) {
      return res.status(404).json({ error: "ROM file missing from storage" });
    }
    res.type("application/octet-stream").sendFile(file);
  } catch (e) {
    next(e);
  }
});
