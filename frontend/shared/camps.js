// The farm's camps, as chips. Shared by Field and Admin: both let a camp be
// picked wherever a location is recorded (a camp move, a movement or birth
// event, a new animal), and both must offer the same list in the same
// order, or a camp picked on the phone is not the one the office sees.
//
// The list comes from GET /api/camps and is kept in localStorage, not
// IndexedDB: it is a few dozen names, and Field's idb.js warns at length
// about what a version bump there costs a phone holding unsynced records.
// With no signal the chips come from the last copy; with no copy at all
// the text box is simply shown, which is what the apps did before camps
// were managed.
//
// Every picker ends with an "Other" chip that reveals the text box. That
// is deliberate: a worker at a gate must never be blocked by a camp the
// office has not set up yet. The server adds whatever is typed to
// Settings (campnames.py), and the apps say so in the toast so the admin
// knows to tidy the list.
const KuddeCamps = {
  STORAGE_KEY: "kudde_camps",
  list: [],

  _readCache() {
    try {
      const raw = localStorage.getItem(KuddeCamps.STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (_) { return []; }
  },

  // Fetches the live list when the server answers, else falls back to the
  // cache. Never throws for a network failure: the caller is loading a
  // screen and a missing camp list must not stop it.
  async load() {
    try {
      const fresh = await Kudde.api("/api/camps");
      KuddeCamps.list = fresh.map((c) => ({ id: c.id, name: c.name }));
      try { localStorage.setItem(KuddeCamps.STORAGE_KEY, JSON.stringify(KuddeCamps.list)); } catch (_) { /* full or private */ }
      Kudde.setOffline(false);
    } catch (e) {
      if (!Kudde.isNetworkError(e)) throw e;
      Kudde.setOffline(true);
      KuddeCamps.list = KuddeCamps._readCache();
    }
    return KuddeCamps.list;
  },

  names() { return KuddeCamps.list.map((c) => c.name); },

  _same(a, b) {
    return String(a || "").trim().toLowerCase() === String(b || "").trim().toLowerCase();
  },

  // True when `name` is already a camp the list knows, whatever its
  // capitals - the same rule the server applies when it snaps a typed
  // location to the spelling in Settings.
  isKnown(name) { return KuddeCamps.list.some((c) => KuddeCamps._same(c.name, name)); },

  // Draws the chips into `container` and wires them to `input`, the text
  // box that actually carries the value (so every existing save path keeps
  // reading one input, online, offline and replayed alike). A chip puts its
  // name in the box and hides it; Other clears and shows it. `exclude` is
  // the camp being moved FROM, which is not a destination.
  render(container, input, { exclude = null } = {}) {
    const names = KuddeCamps.names().filter((n) => !KuddeCamps._same(n, exclude));
    container.innerHTML = "";
    const current = input.value.trim();
    const knownCurrent = current && names.some((n) => KuddeCamps._same(n, current));

    if (!names.length) {
      // Nothing set up (or nothing cached yet): just the box, as before.
      input.classList.remove("hidden");
      return;
    }

    const chip = (label, isOther) => {
      const el = document.createElement("button");
      el.type = "button";
      el.className = "chip" + (isOther ? " chip-other" : "");
      el.textContent = label;
      container.appendChild(el);
      return el;
    };
    const setActive = (el) => {
      container.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c === el));
    };

    for (const name of names) {
      const el = chip(name, false);
      if (KuddeCamps._same(name, current)) el.classList.add("active");
      el.addEventListener("click", () => {
        input.value = name;
        input.classList.add("hidden");
        setActive(el);
      });
    }
    const other = chip("Other\u2026", true);
    other.addEventListener("click", () => {
      input.value = "";
      input.classList.remove("hidden");
      input.focus();
      setActive(other);
    });

    // A value already in the box that no chip matches (an unlisted camp
    // from an older record, say) stays visible rather than being lost.
    if (current && !knownCurrent) {
      input.classList.remove("hidden");
      setActive(other);
    } else {
      input.classList.add("hidden");
    }
  },
};
