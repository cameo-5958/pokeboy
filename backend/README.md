# pokeboy-backend

Stores and serves ROMs, mods, and the cartridge registry for the Pokeboy app.

## Run

```sh
npm install
npm run dev            # http://localhost:4000
```

Configuration is via optional env vars — every path defaults sensibly and is
declared in `src/config.ts`:

- `PORT` — API port (default `4000`).
- `ROMS_DIR` — ROM binaries (default `./roms`).
- `MODS_DIR` — mod payloads (default `./mods`).
- `LABELS_DIR` — label images (default `./labels`).
- `REGISTRY_FILE` — registry catalog (default `./data/registry.json`).
- `KEYS_FILE` — issued device keys (default `./data/keys.json`).
- `CONTENT_SOURCE` — where catalog content is read from: `local` (default) or
  `github`. See [Content source](#content-source).
- `CONTENT_BASE_URL` — raw base URL for `github` mode (default
  `https://raw.githubusercontent.com/cameo-5958/pokeboy/main`).
- `CONTENT_TTL_MS` — in-memory cache TTL for fetched content (default `60000`).
- `CONTENT_TIMEOUT_MS` — per-fetch timeout (default `5000`).
- `GITHUB_TOKEN` — optional PAT for a private repo. Environment only.
- `API_KEY` — legacy single shared secret, always accepted if set.
- `ADMIN_TOKEN` — enables the HTTP key-mint endpoint (`x-admin-token`).
- `TELEMETRY_MCP_BIND` — MCP listener address (default `100.65.93.90`).
- `TELEMETRY_MCP_PORT` — MCP listener port (default `4141`).

## API keys

The API is **open until you mint your first key**, then every `/api/*` request
must send a valid key as an `x-api-key` header. Mint keys locally:

```sh
npm run key:new -- "iphone"   # prints a new key — paste it into the app Settings
npm run key:list              # list issued keys (masked)
npm run key:revoke -- <key>   # revoke one
```

Or over HTTP, if `ADMIN_TOKEN` is set (send `x-admin-token`):

```sh
curl -X POST -H "x-admin-token: $ADMIN_TOKEN" \
     -H 'content-type: application/json' -d '{"label":"iphone"}' \
     http://localhost:4000/api/keys
```

Keys live in `data/keys.json` (git-ignored).

## API

| Method | Path                        | Description                                |
| ------ | --------------------------- | ------------------------------------------ |
| GET    | `/health`                   | Liveness check (unauthenticated).          |
| GET    | `/battle-link`              | Public live trainer command console.       |
| GET    | `/battle-link/decision`     | Public Battle Link long-poll endpoint.     |
| GET    | `/battle-link/pending`      | Pending command requests for the console.  |
| POST   | `/battle-link/command`      | Submit a legal action code.                |
| POST   | `/api/keys`                 | Mint a device key (admin-token guarded).   |
| GET    | `/api/keys`                 | List issued keys, masked (admin-token).    |
| DELETE | `/api/keys/:key`            | Revoke a key (admin-token).                |
| GET    | `/api/registry`             | Combined ROM + mod catalog (versions).     |
| GET    | `/api/cartridges`           | List cartridges.                           |
| GET    | `/api/cartridges/:id`       | One cartridge's metadata.                  |
| GET    | `/api/cartridges/:id/rom`   | Stream the ROM bytes.                      |
| GET    | `/api/mods`                 | List mods.                                 |
| POST   | `/api/telemetry`            | Ingest a telemetry batch.                  |
| GET    | `/api/telemetry`            | List buffered telemetry sessions.          |
| GET    | `/api/telemetry/:key`       | Read one buffered telemetry session.       |
| GET    | `/labels/:file`             | Static cartridge label images.             |

`/api/registry` is what the app diffs against when it "pulls latest": every
entry carries a catalog `version` plus a live content `checksum` (null when the
payload file is absent from disk).

The registry is also the storage source of truth. At startup, and whenever the
registry file's modification time changes, the backend removes unreferenced
regular files from `roms/`, `mods/`, and `labels/`. Dotfiles and subdirectories
are ignored. Per-cartridge symbol files (`mods/<cartridge-id>.sym`) are retained
for cartridges still in the registry. Pruning is skipped if the registry cannot
be read or contains no ROMs and no mods. The re-prune trigger is the *local*
registry file's mtime, so under `CONTENT_SOURCE=github` a remote-only registry
change is picked up on the next restart rather than within a second.

## Content source

Catalog content can be read from local disk or straight from GitHub. One env var
switches it; no code change, and the client contract is identical either way —
the app always talks to this backend, and never to GitHub. That indirection is
the point: a token to read a private repo lives here, on the server, and can
never end up inside a distributed IPA.

`CONTENT_SOURCE=local` (**the default**) reads every path off disk exactly as
before. `CONTENT_SOURCE=github` fetches repo-relative paths from
`CONTENT_BASE_URL`, caches them for `CONTENT_TTL_MS`, and **falls back to the
local file whenever a fetch fails**. The abstraction lives in `src/content.ts`;
`src/storage.ts` (`refs`) names what each piece of content is called in both
places.

| Content                | Repo path                          | `github` mode |
| ---------------------- | ---------------------------------- | ------------- |
| Registry catalog       | `backend/data/registry.json`       | fetched       |
| RGBDS symbols (`.sym`) | `backend/mods/<cartridge>.sym`     | fetched       |
| Mod packages (`.gbmod`)| `backend/mods/<id>.gbmod`          | fetched       |
| Host core (mod-core)   | `app/assets/emulator/mod-core.bin` | fetched       |
| **ROM binaries**       | —                                  | **never**     |

ROMs are the permanent exception: `*.gb` / `*.gbc` are git-ignored because they
are not redistributable, so they exist in no repo and can only ever be served
from `ROMS_DIR`. Label images are likewise still served straight from
`LABELS_DIR` by static middleware, and `GET /api/mods` deliberately keeps
listing what is physically on disk.

### Flipping to raw.githubusercontent

**Today `cameo-5958/pokeboy` is private, so leave this at `local`.** raw
requests to a private repo 404 on every path, and the only fix — shipping a
token — is exactly what must not happen. Once the repo is public, the whole
change is:

```sh
CONTENT_SOURCE=github npm start          # CONTENT_BASE_URL already defaults to
                                         # .../cameo-5958/pokeboy/main
```

Pin a tag or commit SHA instead of a branch to decouple deploys from `main`:

```sh
CONTENT_SOURCE=github \
CONTENT_BASE_URL=https://raw.githubusercontent.com/cameo-5958/pokeboy/v1.4.0 \
npm start
```

To read a **private** repo from a trusted server (never from a device), set
`GITHUB_TOKEN` in the environment — a `.env` file is git-ignored here. It is
sent as `Authorization: Bearer` to `CONTENT_BASE_URL` only, and never reaches a
client. Do not commit it, and do not add it to any app config.

Two behaviors worth knowing before flipping:

- **A failed fetch is not an empty catalog.** Any non-2xx (including the 404 a
  private repo returns for everything) falls back to the local file and logs a
  warning; only a file missing from *both* places reads as absent. A
  misconfigured base URL therefore degrades to today's behavior instead of
  telling every device its catalog was wiped.
- **Pruning follows the served registry.** Orphan pruning (below) acts on
  whatever `/api/registry` is serving, so in `github` mode a remote registry
  governs deletion of local files — including irreplaceable ROMs. The
  empty-registry guard and the local fallback both still apply, but pin
  `CONTENT_BASE_URL` to a known ref if that matters to you.

## Telemetry and MCP

Enable the telemetry toggle in the iOS app to send authenticated batches to
`POST /api/telemetry`. The backend keeps up to 12 minutes of snapshots and 500
events per session in memory; the data is cleared whenever the backend restarts.
REST reads use the same `x-api-key` authentication as the other `/api/*` routes.

The same live data is available to MCP clients through the `list_sessions`,
`get_latest`, `get_series`, and `get_events` tools. Connect with the Streamable
HTTP transport at:

```text
http://100.65.93.90:4141/mcp
```

The MCP listener binds only to the configured Tailscale address and has no
application-level authentication. If that address is unavailable at startup,
the listener logs a warning and falls back to `127.0.0.1` so the main API can
still start. Override the address or port with `TELEMETRY_MCP_BIND` and
`TELEMETRY_MCP_PORT` when testing locally.

## Layout

Paths are configurable (see above); defaults, relative to the backend root:

- `data/registry.json` — the ROM + mod catalog (committed).
- `labels/` — label images (committed).
- `roms/` — ROM binaries (`.gb` / `.gbc` / `.rom`, git-ignored).
- `mods/` — mod payloads (git-ignored).

Drop ROM files matching each `file` field in `data/registry.json` into `roms/`,
then `/api/cartridges/:id/rom` will serve them.
