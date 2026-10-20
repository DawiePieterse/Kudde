// Minimal IndexedDB wrapper for the field app's offline outbox.
//
// One object store ("outbox") holding every create-animal / record-event
// request made while this device couldn't reach the server, each an
// {uuid, kind: "animal"|"event"|"bulk_movement"|"photo"|"calving", payload, synced,
// timestamp}, plus {status: "failed", error, failedAt} once the server has
// refused it (see trySync in app.js).
//
// "failed" lives INSIDE the record rather than in a new store or index on
// purpose: adding either means bumping the database version, and a version
// bump is exactly the moment a phone holding unsynced records is most at
// risk. The upgrade handler below only creates missing stores today, but a
// record field needs no upgrade at all, and old records simply lack it -
// which reads as "not failed", which is right.
// The animal list itself is cached separately (see "animal_cache" below) so
// the herd is still browsable offline, not just capturable.
//
// Every request settles: an IndexedDB request that fails without an
// onerror handler would leave its promise pending forever, and the app
// awaits these on startup, so an unhandled one would freeze the whole UI.
const IDB = (() => {
  const DB_NAME = "kudde_field_db";
  const OUTBOX = "outbox";
  const ANIMAL_CACHE = "animal_cache";
  let dbPromise = null;

  function open() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(OUTBOX)) {
          db.createObjectStore(OUTBOX, { keyPath: "uuid" });
        }
        if (!db.objectStoreNames.contains(ANIMAL_CACHE)) {
          db.createObjectStore(ANIMAL_CACHE, { keyPath: "tag" });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
      req.onblocked = () => reject(new Error("IndexedDB upgrade blocked by another tab"));
    });
    // A failed open must not be cached, or every later call inherits it.
    dbPromise.catch(() => { dbPromise = null; });
    return dbPromise;
  }

  async function tx(store, mode) {
    const db = await open();
    return db.transaction(store, mode).objectStore(store);
  }

  // Resolves when the whole transaction commits, not merely when the
  // request reports success - a write is only durable once the
  // transaction completes.
  function done(store) {
    return new Promise((resolve, reject) => {
      store.transaction.oncomplete = () => resolve();
      store.transaction.onerror = () => reject(store.transaction.error);
      store.transaction.onabort = () => reject(store.transaction.error);
    });
  }

  function request(req) {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  return {
    async enqueue(entry) {
      const store = await tx(OUTBOX, "readwrite");
      store.put({ synced: false, timestamp: new Date().toISOString(), ...entry });
      await done(store);
    },
    // Waiting to be sent: not yet landed and not refused. Failed entries
    // are left out so the sync loop doesn't post the same rejected request
    // every 20 seconds forever.
    async getPending() {
      const store = await tx(OUTBOX, "readonly");
      const all = await request(store.getAll());
      return all.filter((e) => !e.synced && e.status !== "failed");
    },
    // Refused by the server. Kept on the device - not deleted - because
    // each one is a record somebody captured in the field (a weighing, a
    // death) and silently throwing it away is how a herd book goes wrong
    // without anyone noticing. Only the user discards one (discard below).
    async getFailed() {
      const store = await tx(OUTBOX, "readonly");
      const all = await request(store.getAll());
      return all.filter((e) => !e.synced && e.status === "failed");
    },
    async markFailed(uuid, error) {
      const store = await tx(OUTBOX, "readwrite");
      const finished = done(store);
      // The put is issued from inside the get's own callback rather than
      // after an `await`: older iOS Safari commits a transaction as soon as
      // control returns to the event loop, so a put after an awaited get
      // lands on a closed transaction and throws.
      const req = store.get(uuid);
      req.onsuccess = () => {
        const entry = req.result;
        if (entry) {
          store.put({ ...entry, status: "failed", error: String(error || ""), failedAt: new Date().toISOString() });
        }
      };
      await finished;
    },
    async discard(uuid) {
      const store = await tx(OUTBOX, "readwrite");
      store.delete(uuid);
      await done(store);
    },
    async markSynced(uuid) {
      const store = await tx(OUTBOX, "readwrite");
      store.delete(uuid); // nothing needs a synced outbox entry once it's landed
      await done(store);
    },
    async pendingCount() {
      return (await this.getPending()).length;
    },

    // The last full animal list fetched from the server, so the herd is
    // still searchable/browsable with no connection at all. Replaced
    // wholesale on every successful fetch, not merged - a stale local
    // record (e.g. one the admin app changed) should not linger past the
    // next time this device is actually online.
    async cacheAnimals(animals) {
      const db = await open();
      const store = db.transaction(ANIMAL_CACHE, "readwrite").objectStore(ANIMAL_CACHE);
      store.clear();
      for (const a of animals) store.put(a);
      await done(store);
    },
    async getCachedAnimals() {
      const store = await tx(ANIMAL_CACHE, "readonly");
      return request(store.getAll());
    },
  };
})();
