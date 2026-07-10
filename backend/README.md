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
| GET    | `/labels/:file`             | Static cartridge label images.             |

`/api/registry` is what the app diffs against when it "pulls latest": every
entry carries a catalog `version` plus a live content `checksum` (null when the
payload file is absent from disk).

## Layout

Paths are configurable (see above); defaults, relative to the backend root:

- `data/registry.json` — the ROM + mod catalog (committed).
- `labels/` — label images (committed).
- `roms/` — ROM binaries (`.gb` / `.gbc` / `.rom`, git-ignored).
- `mods/` — mod payloads (git-ignored).

Drop ROM files matching each `file` field in `data/registry.json` into `roms/`,
then `/api/cartridges/:id/rom` will serve them.
