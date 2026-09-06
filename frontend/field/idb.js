// Minimal IndexedDB wrapper for the field app's offline outbox.
//
// One object store ("outbox") holding every create-animal / record-event
// request made while this device couldn't reach the server, each an
// {uuid, kind: "animal"|"event"|"bulk_movement"|"photo", payload, synced,
// timestamp}.
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
    async getPending() {
      const store = await tx(OUTBOX, "readonly");
      const all = await request(store.getAll());
      return all.filter((e) => !e.synced);
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
