# Updating a Kudde farm server

How a change you make on your Mac reaches the PC in the farm office, and why
it takes the shape it does. Adapted from what Boord does (MANUAL.md chapter
2), which has been updating real farm servers for a while.

The short version:

- On the farm PC, someone double-clicks **`update_server.bat`**. It fetches
  from GitHub, checks the newest release is signed by the key this server
  trusts, checks it out, installs any new dependencies, copies the database,
  migrates it, and restarts the server.
- Nothing updates itself. **`setup_update_check.bat`** registers a daily
  check that only *tells* you a release is out - the Admin app then says so
  at the top of the screen.
- On your Mac, a release is **`scripts/ship.sh v0.2`**.

---

## Why a signed tag, and not `git pull`

A branch pull trusts whoever can push to the repository. `update_server.bat`
runs elevated, on a machine whose server runs as SYSTEM - so with a plain
`git pull origin main`, a stolen GitHub token would mean code execution on
every farm running Kudde.

Instead a release is a **tag signed with the release key**. `update_server.bat`
refuses to install a tag that is not signed by that specific key, so pushing
code is not enough to ship it; you also have to hold the signing key.

For that check to work the server needs two things: the public key itself,
and which fingerprint to insist on. Only the second is manual.

---

## One-time setup on a farm server

### Getting the server onto a clone it can update

`update_server.bat` runs git commands in the project folder, so the folder
has to be a **git clone** rather than a copied ZIP. On the farm PC:

```bat
git clone https://github.com/DawiePieterse/Kudde.git C:\Kudde
```

Then run `install.bat` from inside `C:\Kudde` as usual. The clone lands on
`main`, which is not a release: after the release-key step below, run
`update_server.bat` once and that is what moves the server onto the newest
signed release, where it stays from then on.

The clone itself is the one moment you are trusting the network rather than
a signature - do it over a connection you control.

Whatever credentials that clone used (an SSH deploy key in
`%USERPROFILE%\.ssh`, or a stored HTTPS credential) have to belong to the
Windows account that will run the daily check - see
[Being told when a release is out](#being-told-when-a-release-is-out).

### Git and GnuPG

`install.bat` handles both: it installs Git if missing, installs Gpg4win,
points git at it, and imports `release-key.asc` if that file is in the
folder.

Git for Windows bundles a `gpg.exe` that does **not** work here - it keeps
keys in a `keyboxd` daemon the Git distribution does not ship, so importing
fails with `probably not installed` and processes zero keys. That is why
Gpg4win is installed separately and git is pointed at it explicitly. By
hand:

```bat
where gpg
git config --global gpg.program "C:/Program Files/GnuPG/bin/gpg.exe"
gpg --import release-key.asc
```

Open a new Command Prompt after installing, since PATH changes do not reach
an already-open window.

### Trusting the release key

**The one manual step.** In the project folder:

```bat
>data\release_key.fpr echo 67C64CFDD584DD140E58AF6E329C9B9DD0562A9D
```

That is the fingerprint of the key in `release-key.asc` - **the same key that
signs Boord releases**, so a farm PC running both records the same 40
characters twice, once per install folder. Confirm it against a source that
is not this repository before you type it (`gpg --fingerprint` on the signing
machine, or the copy in Boord's MANUAL.md chapter 2) - checking a fingerprint
against the repo it is there to protect proves nothing. Type it with the
**redirect first**, exactly as shown. `cmd` reads a digit written
immediately before a `>` as a file handle number, so the more natural
`echo <FINGERPRINT>> file` drops the last character whenever it happens to
be a digit - and the next update then fails its signature check, which reads
as tampering rather than as a typo. Check it:

```bat
type data\release_key.fpr
```

40 characters, nothing else.

> **Why the key can ship in the repository but the fingerprint cannot.**
> They do different jobs. The key is just a key; the fingerprint is the
> decision about which key counts. Someone who could push to the repository
> could swap `release-key.asc` for their own key and sign a release with it -
> and `data\release_key.fpr` is what catches that, because their fingerprint
> would not match the one on file. It lives in `data\`, which is gitignored,
> so no update can rewrite it. Treat that file as the thing that actually
> protects the farm, and change it only when you deliberately rotate the key.

If `data\release_key.fpr` is missing, `update_server.bat` stops and updates
nothing. That is intentional: a server that cannot tell a genuine release
from a tampered one should not be installing either. It also checks that
GnuPG actually runs before trusting any signature check, so a missing gpg
reports itself as a missing gpg rather than as a failed signature.

---

## Installing an update on the farm server

Double-click **`update_server.bat`**. In order, it:

1. reads the trusted fingerprint from `data\release_key.fpr`, and stops if
   there isn't one;
2. checks gpg actually runs;
3. `git fetch --tags --force origin`;
4. picks the newest `v*` tag **by version order**, not by date - a tag's date
   is attacker-controlled, its version number is what humans reason about;
5. **verifies the signature is by the trusted fingerprint**, and stops,
   changing nothing, if it is not;
6. `git checkout --force <tag>`;
7. installs any new dependencies, and stops if they did not all install -
   a release that adds a migration cannot run without them, and a
   half-installed update is worse than no update;
8. **stops the server properly** - not just `schtasks /end`, which ends the
   launcher while the uvicorn process it started can carry on serving with
   the database open. `stop_server.ps1` checks port 8030 is genuinely free
   and refuses rather than guessing;
9. **copies the database**, into `data\backups\pre_migration_*.db`, and
   refuses to migrate if that copy could not be written. The animals' photos
   are taken alongside it, into a matching `pre_migration_*_photos\` folder -
   as hard links, so they take names rather than disk space. Photos are the
   one thing a failure here does *not* stop the migration for: a migration
   rewrites the database, never a photo file;
10. migrates the database in the foreground, so a schema change happens in
    front of the person who chose to update;
11. restarts the server.

If the signature check fails, nothing is changed and the server keeps running
the release it was already on. Do not "fix" this by checking the tag out by
hand - find out why it failed first. The usual innocent cause is a rotated
release key that this server was not told about; the other common one is that
the key was never imported on this PC at all, which the script reports
separately (`The release key is not in this server's keyring`) precisely so
it does not read as tampering.

Afterwards, each phone still needs its app **fully closed and reopened** (not
just backgrounded) to pick up new front-end code - both apps are installed
PWAs with a service worker holding the shell.

### Being told when a release is out

Nothing checks by itself unless you ask it to. Double-click
**`setup_update_check.bat`** once, and a Scheduled Task ("Kudde Update
Check") runs `update_server.bat --check` every morning at 07:30. That looks
for a newer signed release, verifies its signature, and writes what it found
to `data\update_available.json` - **it installs nothing**. The Admin app
reads that (via `/api/version`) and shows a notice at the top of the screen.

Installing stays a deliberate double-click of `update_server.bat` at a time
that suits the farm. An update that applies itself at 03:00 needs a rollback
story to match, which this does not have.

That task runs as **you**, not as SYSTEM, unlike the server. It has to:
fetching from GitHub uses the credentials in your own Windows profile, and
SYSTEM has a different profile with none of them, so a check running as
SYSTEM would fail every day, silently, forever. The trade is that the check
only runs while you are logged on - which is also the only time anyone could
act on what it finds.

Run it by hand at any time:

```bat
update_server.bat --check
```

Failures are recorded too, on purpose: a check that has not succeeded since
April looks exactly like "no updates available" unless it says so.

### Doing it by hand

```bat
git fetch --tags --force origin
git tag --list "v*" --sort=-v:refname
git verify-tag <newest-tag>
git checkout --force <newest-tag>
```

Check the `verify-tag` output names your release key **before** the checkout -
that is the whole point of the exercise. The checkout leaves git in "detached
HEAD", which is normal and correct here: the server tracks releases, not a
branch. It only touches the app's code; the database and the backups live in
the gitignored `data\` folder, and no checkout ever writes there.

Then, **with the server stopped**, bring the database up to date and start it
again:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File stop_server.ps1
backend\.venv\Scripts\python.exe backend\migrate.py
schtasks /run /tn "Kudde Server"
```

A checkout alone restarts nothing - the running server keeps serving the old
code until it is restarted, which is the single easiest step to forget and
the reason `update_server.bat` exists.

### If an update goes wrong

Go back to the release that was running, and restart:

```bat
git checkout --force <previous-tag>
schtasks /run /tn "Kudde Server"
```

If the migration had already run, the database is a version ahead of that
code. The copy taken before it ran is the newest `pre_migration_*.db` in
`data\backups\` - stop the server, move the current `data\kudde.db` aside
(don't delete it), and copy that file into its place.

Then put back the photos that go with it, from the `pre_migration_*_photos\`
folder of the same name:

```
robocopy data\backups\pre_migration_<the same one>_photos data\photos /E /XC /XN /XO
```

Copy rather than move, and let it add to `data\photos\` rather than replace
it: anything already there is either the same photo or one taken after the
snapshot, and neither is worth losing. Photos taken after the snapshot are
not in the restored database, so they simply sit unreferenced on disk until
you roll forward again.

---

## Cutting a release (on your Mac)

Farm servers install signed tags, so pushing to `main` deploys nothing on its
own.

The whole release in one command, runnable from any directory:

```
~/Documents/Kudde/scripts/ship.sh v0.2
```

It refuses to ship from a branch other than `main` or with a dirty working
tree, offers the `frontend/shared/api.js` version bump if the constant is
still on the old number (as its own commit), pushes `main`, runs the signing
helper below, and asks once more before pushing the tag - that last pause is
where you check the fingerprint.

To do the signing step on its own:

```
scripts/release.sh v0.2
```

It refuses to proceed unless the working tree is clean, the tag looks like
`v<number>`, that tag doesn't already exist, a `user.signingkey` is
configured, and `Kudde.VERSION` in `frontend/shared/api.js` matches the tag.
That last check matters more than it looks: `Kudde.VERSION` is what the Field
and Admin headers print, and it is how you tell at a glance whether a device
actually picked up an update.

The tag is signed and verified locally, but **not pushed**. Push it when you
are ready for farms to see it:

```
git push origin v0.2
```

Never re-point a tag that has already been pushed. Servers compare the tag
they are on against the newest tag, so a moved tag silently fails to
re-deploy on any server already sitting on it. Cut a new version instead.

### Setting up signing, once

Kudde is signed with the same key as Boord, so if you already ship Boord
there is nothing to set up beyond pointing this repo's git at it:

```
git config user.signingkey 67C64CFDD584DD140E58AF6E329C9B9DD0562A9D
```

For a new key instead (which would mean a second fingerprint for every farm
to record):

```
gpg --full-generate-key            # if you don't already have a key
gpg --list-secret-keys --keyid-format=long
git config user.signingkey <KEY-ID>
gpg --armor --export <KEY-ID> > release-key.asc     # commit this
gpg --fingerprint <KEY-ID>         # the 40 characters each farm records
```

`release-key.asc` in this repository is only a convenience for installers.
The fingerprint each server records in `data\release_key.fpr` is what
actually decides which releases it accepts.

Rotating the key means visiting every farm: until `data\release_key.fpr` is
updated there, `update_server.bat` refuses the new releases as unsigned by
the key it trusts - which is the mechanism working, not a fault.

---

## Checking what a server is running

```
https://<farm-pc>.<tailnet>.ts.net:8030/api/version
```

Over Tailscale, from any connected device. The farm-wifi address and this
PC's own `localhost` are both refused now - `/healthz` is the only thing
that answers off the tailnet, and it deliberately says nothing but `ok`.

The `:8030` matters on a PC that also runs Boord: Kudde's `tailscale serve`
mapping lives on its own port (see install.ps1's setup message) precisely so
it can't silently steal Boord's bare `https://...ts.net/` slot, or vice
versa. Leaving the port off this URL on a shared box reaches whichever app
currently owns that slot, not necessarily Kudde - see "If another app is on
this machine too" in Boord's MANUAL.md chapter 2 for the failure mode
(`{"detail":"Not Found"}` from a perfectly healthy wrong app).

```json
{
  "version": "0.2", "tag": "v0.2", "state": "release",
  "frontend_version": "0.2", "matches": true,
  "alembic_head": "063aa8282327", "alembic_current": "063aa8282327",
  "migrations_applied": true,
  "update": { "latest": "v0.3", "available": true, "signature": "ok",
              "checked_at": "2026-09-08T07:30:01+02:00" }
}
```

`state` is one of `release` (on a signed tag), `ahead` (a checkout between
releases, e.g. a dev machine), `reported` (git could not answer, so this is
the tag `update_server.bat` last recorded) or `unknown`.

The git side is read once at startup, on purpose: it reports what this
**process** booted from. So a checkout done by hand without a restart shows
the server on the old version while a reloaded browser header shows the new
one - and that disagreement is the signal, because "the code changed but
nothing restarted" is exactly the state that otherwise goes unnoticed for
weeks. `migrations_applied: false` means the same thing about the database.
