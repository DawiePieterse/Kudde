# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Kudde ("herd" in Afrikaans) is a small cattle-recording app: animals, plus the handful of events that happen to them (birth, weighing, treatment, move between camps, sale, death). It runs on the farm PC beside Boord and its siblings, on port **8030**, published by `tailscale serve --https=8030`.

- `MANUAL.md` is the user and admin manual. Read it for behaviour and anything user-facing.
- `UPDATING.md` is the full reference for how a release reaches the farm PC.

It shares no database or code with Boord. Boord's `watchdog.ps1` and `heartbeat.ps1` watch it, and Boord Monitor shows it.

## The Boord suite

This app is one of six that run together on the farm PC (`bekfontein-server`), each its own repo, process, port and database. They are reached only over Tailscale, and each binds `127.0.0.1` and is published with `tailscale serve`. The same section is in every app's `CLAUDE.md`. When a fact here changes, change it in all six.

| App | Repo (`DawiePieterse/…`) | What it does | Port | Tailnet address | Mac folder | Farm PC folder |
|---|---|---|---|---|---|---|
| Boord | `Boord` | Harvest capture and picker pay per kg: field tablets, pack-house receiving, admin office. The hub. | 8000 | `https://bekfontein-server.taile32cb0.ts.net/` | `~/Documents/Projects/Boord` | `C:\Boord` |
| Boord Owner | `boordowner` | The owner's view: season vs history, weather, risk, crop estimate, optional AI. | 8010 | `https://bekfontein-server.taile32cb0.ts.net:8443/` | `~/Documents/Projects/BoordOwner` | `C:\BoordOwner` |
| Boord Notes | `boord-notes` | Private farm notebook for Andre and his son, offline-first, optional AI. | 8020 | `https://bekfontein-server.taile32cb0.ts.net:9443/` | `~/Documents/Projects/BoordNotes` | `C:\bekfontein-farm-notebook` |
| **Kudde** (this app) | `Kudde` | Cattle records: animals and their events. | 8030 | `https://bekfontein-server.taile32cb0.ts.net:8030/` | `~/Documents/Projects/Kudde` | `C:\Kudde` |
| Boord Labour | `Boord-labour` | Packhouse time clock: badge scans at the door, hourly pay with overtime. | 8040 | `https://bekfontein-server.taile32cb0.ts.net:8040/` | `~/Documents/Projects/Boord-labour` | `C:\Boord-labour` |
| Boord Monitor | `Boord-monitor` | Watches the PC and the other five apps; Web Push alerts and a Restart button. | 8050 | `https://bekfontein-server.taile32cb0.ts.net:8050/` | `~/Documents/Projects/Boord-monitor` | `C:\boord-monitor` |

**How they depend on each other:**
- **Boord is the hub.** Its `data/boord.db` is opened read-only by Owner (harvest, lots, blocks, suppliers), Labour (the worker register, and Boord's `data/photos` for faces) and Monitor (tablet station, last sync and outbox).
  - Boord is that database's only writer and migrates it at startup.
  - Owner and Labour refuse to boot if a column they read disappears. A Boord migration that drops or renames a column needs those apps updated in step.
- **Labour's badges are Boord's.** The badge QR holds exactly `worker.id` (`Boord/frontend/admin/print-badge.html`), and Labour vendors Boord's `frontend/shared/`.
- **Owner asks Notes.** The "Ask the farm notes" bridge calls Notes' `/api/ai/ask` at `OWNER_NOTES_URL` (`http://127.0.0.1:8020`) and can share Notes' `data/anthropic_key.txt`.
- **Boord keeps the others running.** Boord's `watchdog.ps1` (restarts an app that stops answering) and `heartbeat.ps1` cover all six.
  - Their app table (name, port, task, health path) must match Monitor's `backend/apps.py` and the port table above. Change them together.
  - Monitor reads Boord's `data/watchdog.log`, and its Restart button runs Boord's `watchdog.ps1 -Restart "<Name>"`.
- **Monitor watches everything.** It shows each app's up/down state and `/api/version`, Tailscale and the PC, and pushes an alert after three failed checks in a row.
  - Every app's `/api/version` keeps the same field names (`tag`, `state`, `frontend_version`), because Monitor and the heartbeat read them all.
  - An off-site copy of the monitor on the owner's Mac alerts when the whole farm PC is unreachable.
- **Kudde and Notes stand alone.** No other app reads their data. They are only watched and restarted.

**What they share by convention, not code:**
- One release key: fingerprint `67C64CFDD584DD140E58AF6E329C9B9DD0562A9D` in each app's `data\release_key.fpr`.
- Releases are signed tags, made from an up-to-date `main` on the Mac and installed by each app's updater on the farm PC.
- `stop_server.ps1` stops each server, never a bare `schtasks /end`. Labour's copy is the reference.
- Each installer adds a Git `safe.directory` exception so SYSTEM's Git can read the repo folder.
- Python 3.9-compatible code, plain JS with no CDNs (the phones are tailnet-only), and `VERSION` plus the service-worker `CACHE` bumped with every frontend change.
- Fixes to the copied deployment files (`install.ps1`, the updaters, `stop_server.ps1`, `release.sh`) flow between the repos.

## Commands

All backend commands run from `backend/`.

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                                   # all tests, no network
.venv/bin/python -m pytest tests/test_events.py -q              # one file
KUDDE_ALLOW_LOOPBACK=1 KUDDE_DATA_DIR=/tmp/kudde-dev \
  .venv/bin/python -m uvicorn main:app --port 8030              # dev server, http://localhost:8030/admin/
.venv/bin/python -m alembic revision --autogenerate -m "what changed"   # new migration
../scripts/ship.sh v0.10    # push main, then sign and push the tag (wraps release.sh)
```

Keep code Python 3.9-compatible, the farm's minimum: no `X | None`, no `match`.

CI (`.github/workflows/tests.yml`) runs pytest on Python 3.9, the floor `install.ps1` accepts, and 3.11, which it installs. The tests set `KUDDE_DATA_DIR` to a temporary folder in `tests/conftest.py` before importing `db`.

## Architecture

- **Two screens, no device setup:**
  - `frontend/field/` works offline and is for whoever is out with the herd;
  - `frontend/admin/` is the office.
  - A device is whichever screen it opens.
- **The network is the password** (`security.py`). There is no sign-in. Kudde answers only requests that arrived over Tailscale, the field screen included.
  - That includes the PC's own console, because AnyDesk is how it is reached.
  - Only `/healthz` answers off Tailscale. It says nothing about the herd, and the installer's post-install check needs it.
  - `KUDDE_ALLOW_LOOPBACK=1` is for local development only. The farm's launcher never sets it.
  - The server binds 127.0.0.1. Never bind 0.0.0.0.
- **Field capture is offline-first.** `frontend/field/idb.js` holds an outbox of creates and events, plus a cached animal list so the herd can be browsed offline.
  - Each request carries a phone-made `client_uuid`, and the server is idempotent on it, so a replay is harmless.
  - A refused request is marked `status: "failed"` inside its record. A new store or index would mean bumping the IndexedDB version, and that is when a phone holding unsynced records is most at risk.
- **Store identity and events; derive the rest.** Where an animal is, its last weight, withdrawal periods and whether a cow has calved are all read off the event history in one place, `herd.py`, so every screen gets the same answer.
  - `classes.py` derives cow, heifer, bull, steer and calf from sex, birth date, the castrated flag and calvings.
  - A death or sale event sets the animal's status. There is no separate status field to forget.
- **Names ignore capitals, compared in Python not SQL** (`campnames.py`), because SQLite's case folding mishandles Afrikaans diacritics.
  - A typed camp snaps to the spelling Settings holds.
  - An unknown camp is added as it is used, so a worker at a gate is never blocked.
  - Event locations stay plain text.
- **Schema changes are Alembic migrations** (`backend/migrations/`, run by `migrate.py`).
  - The database is copied first (`backup.py`), and nothing migrates if the copy can't be written.
  - Photos are copied too, but a failed photo copy doesn't block the migration.
  - Drift between `models.py` and the live schema is printed at startup.

## The farm PC

- **Releases are signed tags; merging to `main` deploys nothing.**
  - `update_server.bat` picks the newest `v*` tag and verifies it against `data\release_key.fpr`.
  - It stops the server with `stop_server.ps1`, copies the data, migrates and restarts.
  - `setup_update_check.bat` registers a daily check that only reports. Admin then shows a notice.
- **Tag only from an up-to-date `main`, and never move a released tag.** `release.sh` checks `VERSION` against the tag, but not that your clone is current: Boord Notes' `v2.16` was signed in a stale clone and points at a months-old commit. Run `git pull --ff-only origin main` first. Fix a wrong tag with a new release.
- **`stop_server.ps1`, never a bare `schtasks /end`.**
  - Ending the task leaves uvicorn holding port 8030, so the new release can't start and the old one keeps serving.
  - `stop_server.ps1` counts only loopback and all-interface listeners, because `tailscale serve --https=8030` makes tailscaled listen on the same port. Kudde found this first.
- **The launcher `install.ps1` writes** restarts the server 15 seconds after it exits and logs to `data\server.log`. The installer also reserves port 8030, so Hyper-V, WSL or Docker can't take it at boot.
- **The app table in Boord's `watchdog.ps1` and `heartbeat.ps1`**, and Boord Monitor's `apps.py`, list Kudde's port, task name ("Kudde Server") and health path (`/healthz`). Change them together.

## Frontend

- **Plain JS, no build step, no CDNs.** The phones are tailnet-only. `shared/tailwind.js` and `shared/vendor/` are served locally.
- **Any frontend change needs two bumps:**
  - `VERSION` in `frontend/shared/api.js`, checked by `release.sh`;
  - `CACHE` in the `service-worker.js` of each screen whose files changed.
- **Never bump the IndexedDB version to add a field.** Put new per-record data inside the record. Old records simply lack it.

## Installing and deploying

### Where it lives

| | |
|---|---|
| Your Mac (the only clone to release from) | `~/Documents/Projects/Kudde` |
| Farm PC folder | `C:\Kudde` |
| Farm PC scheduled task | "Kudde Server", runs as SYSTEM at boot |
| Port on the farm PC | `127.0.0.1:8030` |
| Address on the tailnet | `https://bekfontein-server.taile32cb0.ts.net:8030/` |

### First install on the farm PC (once)

Only for a new PC or a reinstall. An app that is already running is updated, not reinstalled.

1. Open a Command Prompt and clone the repo into the folder above:
   ```bat
   git clone https://github.com/DawiePieterse/Kudde.git C:\Kudde
   ```
2. In `C:\Kudde`, double-click **`install.bat`** and accept the administrator prompt.
   It installs Python if needed, builds the app's environment, writes the launcher, registers the scheduled task, reserves the port so Windows can't hand it to Hyper-V, WSL or Docker at boot, and starts the server. Re-running it is safe and keeps the data.
3. Tell the updater which release key to trust. In the same folder, type it exactly like this, with the redirect first:
   ```bat
   >data\release_key.fpr echo 67C64CFDD584DD140E58AF6E329C9B9DD0562A9D
   ```
4. Then double-click `setup_update_check.bat` once: a daily check that makes Admin show a notice when a release is out.
5. Publish it on the tailnet, in a Command Prompt opened as administrator:
   ```bat
   tailscale serve --bg --https=8030 http://localhost:8030
   ```
6. If the installer did not already add it, let SYSTEM's Git read the folder, as administrator (forward slashes):
   ```bat
   git config --system --add safe.directory C:/Kudde
   ```
7. Check it from your Mac: `curl -s https://bekfontein-server.taile32cb0.ts.net:8030/api/version` should show the release tag.

### Deploying a change (every release)

Merging to `main` changes nothing on the farm. A release is a signed tag, and the farm PC installs it when its updater runs.

1. **Merge the PR on GitHub.** A frontend change must already bump `VERSION` in `frontend/shared/api.js` and the service worker's `CACHE`.
2. **On your Mac, bring the clone up to date.** This step is what stops a tag landing on an old commit:
   ```sh
   cd ~/Documents/Projects/Kudde && git checkout main && git pull --ff-only origin main
   git log -1 --format='%h %s'                  # the merge you expect
   grep -m1 'VERSION:' frontend/shared/api.js   # the version you are releasing
   ```
3. **Sign and push the tag** (`vX.Y` must equal `VERSION`):
   ```sh
   scripts/ship.sh vX.Y        # signs, verifies and pushes the tag
   ```
   Never move or reuse a released tag. Fix a wrong one with the next version.
4. **On the farm PC**, in `C:\Kudde`, double-click **`update_server.bat`**. It verifies the signature, stops the server with `stop_server.ps1`, installs dependencies, copies and migrates the database, and starts it again. If it stops with an error, nothing has changed. Read the message; never check the tag out by hand.
5. **If the release changed `install.ps1`** (its notes say "run install.bat once more"), double-click `install.bat` as well. The updater does not rewrite the launcher or the task.
6. **Check from your Mac:** `curl -s https://bekfontein-server.taile32cb0.ts.net:8030/api/version` should show `"tag":"vX.Y","state":"release"`.
7. **Close and reopen the Field and Admin apps on each phone so they load the new version.**

## Conventions

Comments are long on purpose. They explain *why*, usually a farm constraint: cattle out of signal range, one person doing the office work, a PC reached over AnyDesk that nobody administers. Keep that style.
