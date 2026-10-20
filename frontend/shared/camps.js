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

  async load() {
    const fresh = await Kudde.cachedList(KuddeCamps.STORAGE_KEY, "/api/camps");
    KuddeCamps.set(fresh);
    return KuddeCamps.list;
  },

  // Takes a list already fetched (Admin's Settings loads every camp,
  // archived too, and hands the active ones here) so the chips follow a
  // rename or an archive without a second round trip.
  set(list) {
    KuddeCamps.list = list.filter((c) => !c.archived).map((c) => ({ id: c.id, name: c.name }));
    try { localStorage.setItem(KuddeCamps.STORAGE_KEY, JSON.stringify(KuddeCamps.list)); } catch (_) { /* full or private */ }
  },

  names() { return KuddeCamps.list.map((c) => c.name); },

  // The comparison form of a camp name: whitespace collapsed, case
  // ignored - the same rule as campnames.fold() on the server, so a camp
  // the server would snap to is one the phone knows too.
  key(name) { return String(name || "").trim().split(/\s+/).join(" ").toLowerCase(); },

  _same(a, b) { return KuddeCamps.key(a) === KuddeCamps.key(b); },

  // True when `name` is already a camp the list knows, whatever its
  // capitals - the same rule the server applies when it snaps a typed
  // location to the spelling in Settings.
  isKnown(name) { return KuddeCamps.list.some((c) => KuddeCamps._same(c.name, name)); },

  // The toast suffix after a save that may have added a camp through the
  // Other chip: the server adds it to Settings on its own, and the admin
  // needs to know to tidy it. Empty when the camp was known.
  newCampNote(name) {
    return name && !KuddeCamps.isKnown(name) ? " (new camp added to Settings)" : "";
  },

  // The Move camp picker's first step, the same in both apps: every camp
  // with its count, empty ones greyed (nothing to move FROM), and the row
  // with no location, the animals never placed anywhere, so they can be
  // moved INTO a camp. `onPick(index)` gets the row tapped.
  renderLocations(listEl, emptyEl, locations, onPick) {
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    emptyEl.classList.toggle("hidden", locations.length > 0);
    listEl.innerHTML = locations.map((loc, i) => {
      const n = loc.animals.length;
      const label = loc.location === null ? "No camp yet" : loc.location;
      return `
      <button type="button" class="w-full text-left rounded-lg p-3 flex items-center justify-between move-location-btn ${n ? "bg-slate-50 hover:bg-slate-100" : "bg-slate-50 opacity-50"}"
              data-index="${i}" ${n ? "" : "disabled"}>
        <span class="font-semibold ${loc.location === null ? "italic" : ""}">${esc(label)}</span>
        <span class="text-xs text-slate-500">${n ? `${n} animal${n === 1 ? "" : "s"}` : "empty"}</span>
      </button>`;
    }).join("");
    listEl.querySelectorAll(".move-location-btn").forEach((btn) => {
      btn.addEventListener("click", () => onPick(parseInt(btn.dataset.index, 10)));
    });
  },

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
