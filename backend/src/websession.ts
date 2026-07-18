/**
 * Cookie sessions for the browser player (emulator.cameo.moe).
 *
 * Single-account auth: WEB_USERNAME/WEB_PASSWORD (backend/.env) are checked at
 * login, then a signed expiry token rides an HttpOnly cookie — `exp.hmac`, no
 * server-side session store. Everything fails closed while the credentials or
 * WEB_SESSION_SECRET are unset.
 */
import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import type { Request, Response } from "express";

import { config } from "./config.js";

export const SESSION_COOKIE = "pokeboy_web";
const SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000;

function configured(): boolean {
  return Boolean(config.webUsername && config.webPassword && config.webSessionSecret);
}

/** Constant-time string comparison (hash first so lengths never leak). */
function digestEquals(a: string, b: string): boolean {
  return timingSafeEqual(
    createHash("sha256").update(a).digest(),
    createHash("sha256").update(b).digest(),
  );
}

export function checkCredentials(username: unknown, password: unknown): boolean {
  if (!configured()) return false;
  if (typeof username !== "string" || typeof password !== "string") return false;
  const userOk = digestEquals(username, config.webUsername);
  const passOk = digestEquals(password, config.webPassword);
  return userOk && passOk;
}

function sign(exp: number): string {
  return createHmac("sha256", config.webSessionSecret).update(String(exp)).digest("hex");
}

/** Exported for tests (expired-token minting); production goes through issueSessionCookie. */
export function mintToken(exp: number): string {
  return `${exp}.${sign(exp)}`;
}

export function issueSessionCookie(req: Request, res: Response): void {
  res.cookie(SESSION_COOKIE, mintToken(Date.now() + SESSION_TTL_MS), {
    httpOnly: true,
    sameSite: "lax",
    secure: req.secure,
    path: "/",
    maxAge: SESSION_TTL_MS,
  });
}

export function clearSessionCookie(res: Response): void {
  res.clearCookie(SESSION_COOKIE, { path: "/" });
}

function cookieValue(req: Request, name: string): string | null {
  const header = req.headers.cookie;
  if (!header) return null;
  for (const part of header.split(";")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() === name) return decodeURIComponent(part.slice(eq + 1).trim());
  }
  return null;
}

export function hasWebSession(req: Request): boolean {
  if (!configured()) return false;
  const token = cookieValue(req, SESSION_COOKIE);
  if (!token) return false;
  const dot = token.indexOf(".");
  if (dot <= 0) return false;
  const exp = Number(token.slice(0, dot));
  if (!Number.isFinite(exp) || exp < Date.now()) return false;
  return digestEquals(token.slice(dot + 1), sign(exp));
}

// Login rate limit: 10 failures per 15 minutes per client IP, in memory.
const WINDOW_MS = 15 * 60 * 1000;
const MAX_FAILURES = 10;
const failures = new Map<string, number[]>();

export function loginBlocked(ip: string): boolean {
  const now = Date.now();
  const recent = (failures.get(ip) ?? []).filter((t) => now - t < WINDOW_MS);
  failures.set(ip, recent);
  return recent.length >= MAX_FAILURES;
}

export function noteLoginFailure(ip: string): void {
  const list = failures.get(ip) ?? [];
  list.push(Date.now());
  failures.set(ip, list);
}
