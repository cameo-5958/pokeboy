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
be read or contains no ROMs and no mods.

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
