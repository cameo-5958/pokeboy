/**
 * Serves the exported web build (`app/dist`) over HTTP.
 *
 * This is a test harness, not a deployment: it exists so the same bundle the
 * headless suite drives can be opened in a real browser. Build it first with
 *   EXPO_PUBLIC_API_URL=http://<host>:4000 npx expo export -p web --output-dir dist
 *
 * Usage: node scripts/serve-web.mjs [--host H] [--port P] [--dir D]
 */
import { createServer } from "node:http";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index === -1 ? fallback : args[index + 1];
};

const host = flag("host", process.env.POKEBOY_WEB_HOST ?? "127.0.0.1");
const port = Number(flag("port", process.env.POKEBOY_WEB_PORT ?? 47823));
const root = resolve(flag("dir", "dist"));

// `application/wasm` is required for WebAssembly.instantiateStreaming; without
// it emscripten silently falls back to the slower ArrayBuffer path.
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".wasm": "application/wasm",
  ".css": "text/css; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".ttf": "font/ttf",
  ".woff2": "font/woff2",
  ".bin": "application/octet-stream",
  ".map": "application/json; charset=utf-8",
};

async function resolveFile(pathname) {
  // normalize() collapses ".." before we join, so the served tree is a jail.
  const relative = normalize(decodeURIComponent(pathname)).replace(/^(\.\.[/\\])+/, "");
  let candidate = join(root, relative);
  if (!candidate.startsWith(root)) return null;
  try {
    const info = await stat(candidate);
    if (info.isDirectory()) candidate = join(candidate, "index.html");
  } catch {
    // expo `output: single` is an SPA: unknown paths are client-side routes.
    candidate = join(root, "index.html");
  }
  try {
    await stat(candidate);
    return candidate;
  } catch {
    return null;
  }
}

createServer(async (request, response) => {
  const { pathname } = new URL(request.url, "http://localhost");
  const file = await resolveFile(pathname);
  if (!file) {
    response.writeHead(404, { "content-type": "text/plain" });
    response.end("not found");
    return;
  }
  response.writeHead(200, {
    "content-type": TYPES[extname(file)] ?? "application/octet-stream",
    // The bundle is content-hashed, but this is a test server: never let a
    // stale asset be the reason a run disagrees with the device.
    "cache-control": "no-store",
  });
  createReadStream(file).pipe(response);
}).listen(port, host, () => {
  console.log(`pokeboy web harness: http://${host}:${port}  (serving ${root})`);
});
