# pokeboy-backend

Stores and serves ROMs, mods, and cartridge metadata for the Pokeboy app.

## Run

```sh
npm install
npm run dev            # http://localhost:4000
```

Configuration is via optional env vars (sensible defaults if unset):

- `PORT` — API port (default `4000`).
- `STORAGE_DIR` — path to the storage root (default `./storage`).

## API

| Method | Path                        | Description                          |
| ------ | --------------------------- | ------------------------------------ |
| GET    | `/health`                   | Liveness check.                      |
| GET    | `/api/cartridges`           | List cartridges.                     |
| GET    | `/api/cartridges/:id`       | One cartridge's metadata.            |
| GET    | `/api/cartridges/:id/rom`   | Stream the ROM bytes.                |
| GET    | `/api/mods`                 | List mod payloads (filesystem stub). |
| GET    | `/labels/:file`             | Static cartridge label images.       |

## Storage

Everything lives under `storage/` (configurable via `STORAGE_DIR`):

- `cartridges.json` — the cartridge catalog (committed).
- `labels/` — label images (committed).
- `roms/` — ROM binaries (`.gb` / `.gbc`, git-ignored).
- `mods/` — mod payloads (git-ignored).

Drop ROM files matching the `file` field in `cartridges.json` into `roms/`,
then `/api/cartridges/:id/rom` will serve them.
