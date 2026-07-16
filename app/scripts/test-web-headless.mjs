/**
 * Headless smoke test for the exported web build.
 *
 * Drives the same bundle a browser would load and asserts against embed.html's
 * own postMessage protocol — `ready`, `telemetry`, `error` — rather than
 * against anything test-specific. If this passes, the emulator booted, linked
 * its mods, and is advancing frames.
 *
 * Usage:
 *   node scripts/test-web-headless.mjs --url http://host:47823 --key <api-key>
 * Env: POKEBOY_API_KEY, POKEBOY_BACKEND_URL, POKEBOY_CHROME
 */
import { readdirSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { chromium } from "playwright";

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index === -1 ? fallback : args[index + 1];
};

const url = flag("url", process.env.POKEBOY_WEB_URL ?? "http://127.0.0.1:47823/");
const apiKey = flag("key", process.env.POKEBOY_API_KEY ?? "");
const backendUrl = flag("backend", process.env.POKEBOY_BACKEND_URL ?? "http://127.0.0.1:4000");
const shot = flag("out", "");
const bootTimeoutMs = Number(flag("timeout", 45000));

/**
 * Playwright pins one browser revision, but this host may already carry a
 * different one (and cannot download: Ubuntu 26.04 is unsupported upstream).
 * Prefer an explicit path, then the newest cached build, then the default.
 */
function findChrome() {
  if (process.env.POKEBOY_CHROME) return process.env.POKEBOY_CHROME;
  const root = join(homedir(), ".cache", "ms-playwright");
  try {
    const builds = readdirSync(root)
      .filter((name) => /^chromium-\d+$/.test(name))
      .sort((a, b) => Number(b.split("-")[1]) - Number(a.split("-")[1]));
    if (builds.length) return join(root, builds[0], "chrome-linux64", "chrome");
  } catch {
    /* fall through to Playwright's own resolution */
  }
  return undefined;
}

const failures = [];
const check = (ok, label) => {
  console.log(`${ok ? "  ok  " : " FAIL "} ${label}`);
  if (!ok) failures.push(label);
};

const browser = await chromium.launch({ executablePath: findChrome() });
const context = await browser.newContext({ viewport: { width: 480, height: 900 } });

await context.addInitScript(
  ([key, backend]) => {
    // Seed what a first-run user would type into Settings.
    localStorage.setItem(
      "pokeboy.settings.v1",
      JSON.stringify({ backendUrl: backend, apiKey: key, telemetry: true, devMode: false }),
    );
    // Capture the emulator's own outbound protocol; the iframe posts to parent.
    window.__pokeboy = [];
    window.addEventListener("message", (event) => {
      if (typeof event.data !== "string") return;
      try {
        window.__pokeboy.push(JSON.parse(event.data));
      } catch {
        /* not ours */
      }
    });
  },
  [apiKey, backendUrl],
);

const page = await context.newPage();
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));

console.log(`pokeboy headless: ${url}`);
await page.goto(url, { waitUntil: "domcontentloaded" });

const messages = async () => page.evaluate(() => window.__pokeboy ?? []);
const waitFor = async (predicate, timeout, label) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const found = (await messages()).find(predicate);
    if (found) return found;
    await page.waitForTimeout(250);
  }
  throw new Error(`timed out waiting for ${label}`);
};

let ready = null;
try {
  ready = await waitFor((m) => m.type === "ready", bootTimeoutMs, "emulator ready");
} catch (error) {
  const errs = (await messages()).filter((m) => m.type === "error");
  console.log(`\n${error.message}`);
  for (const e of errs) console.log(`  emulator error: ${JSON.stringify(e.detail).slice(0, 300)}`);
  for (const e of pageErrors) console.log(`  page error: ${e}`);
  await browser.close();
  process.exit(1);
}
check(Boolean(ready), `emulator booted (cartridge: ${ready.detail})`);

// The framebuffer must be advancing, and it must contain a picture.
const frame = page.frames().find((f) => /embed/.test(f.url()));
const sample = async () =>
  frame.evaluate(() => {
    const data = document
      .getElementById("screen")
      .getContext("2d")
      .getImageData(0, 0, 160, 144).data;
    let hash = 0;
    const shades = new Set();
    for (let i = 0; i < data.length; i += 4) {
      hash = (Math.imul(hash, 31) + data[i]) >>> 0;
      shades.add(data[i]);
    }
    return { hash, shades: shades.size };
  });

check(Boolean(frame), "emulator document mounted in an iframe");

// Poll rather than sample once: the boot animation legitimately passes through
// solid-colour frames, so a single instantaneous read proves nothing.
let picture = null;
const pictureDeadline = Date.now() + 15000;
while (Date.now() < pictureDeadline) {
  const current = await sample();
  if (current.shades > 1) {
    picture = current;
    break;
  }
  await page.waitForTimeout(250);
}
check(Boolean(picture), `framebuffer has a picture (${picture?.shades ?? 1} distinct shades)`);

const before = picture ?? (await sample());
await page.waitForTimeout(1000);
const after = await sample();
check(before.hash !== after.hash, "framebuffer advances between samples");

const telemetry = (await messages()).filter((m) => m.type === "telemetry").pop();
check(Boolean(telemetry), "telemetry sampled");
if (telemetry) {
  const { emuFps, rafFps } = telemetry.detail;
  // Do NOT assert an absolute fps. Pacing is driven by requestAnimationFrame,
  // so the rate is a property of the host (a loaded CI box, a headless browser
  // and a 120Hz phone all differ legitimately). The invariant that actually
  // matters is that emulation keeps pace with whatever frame clock it is given.
  check(emuFps > 0, `emulation advancing (emuFps=${emuFps}, rafFps=${rafFps})`);
  check(
    rafFps === 0 || emuFps >= rafFps * 0.8,
    `emulation keeping pace with the frame clock (${emuFps}/${rafFps})`,
  );
}

const errors = (await messages()).filter((m) => m.type === "error");
check(errors.length === 0, `no emulator errors${errors.length ? `: ${JSON.stringify(errors[0].detail).slice(0, 200)}` : ""}`);
check(pageErrors.length === 0, `no uncaught page errors${pageErrors.length ? `: ${pageErrors[0]}` : ""}`);

if (shot) {
  await page.screenshot({ path: shot });
  console.log(`  screenshot: ${shot}`);
}

await browser.close();
console.log(failures.length ? `\nFAILED (${failures.length})` : "\nPASS");
process.exit(failures.length ? 1 : 0);
