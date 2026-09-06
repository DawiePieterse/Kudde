// Kudde Field app. One page, a handful of modals, no framework - matching
// Boord's field/admin apps. Everything works offline: the animal list is
// cached in IndexedDB, and every create/record action either reaches the
// server immediately or is queued in IDB's outbox and replayed once a
// connection comes back (see idb.js and trySync() below).

const EVENT_FIELD_CONFIG = {
  weight: { title: "Record weight", showValue: true, valueLabel: "Weight (kg)", showLocation: false },
  treatment: { title: "Record treatment", showValue: false, showLocation: false },
  movement: { title: "Record movement", showValue: false, showLocation: true },
  // Birth carries a location (which camp it was born in) and leans on the
  // note for what actually matters afterwards - calving ease, whether it
  // had to be pulled, whether cow and calf are up and sucking.
  birth: { title: "Record birth", showValue: false, showLocation: true },
  sale: { title: "Record sale", showValue: false, showLocation: false },
  death: { title: "Record death", showValue: false, showLocation: false },
};

const STATUS_LABEL = { alive: "Alive", dead: "Dead", sold: "Sold" };

let animals = [];       // last known full herd, from the server or the IDB cache
let dashboard = null;   // last known /api/dashboard payload (server-only - not cached)
let activeTag = null;   // tag shown in the detail modal, for the event modal to act on
let activeEventKind = null;

function uuid() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// ---------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------

function renderAnimalList() {
  const needle = document.getElementById("searchInput").value.trim().toLowerCase();
  const filtered = needle
    ? animals.filter((a) => a.tag.toLowerCase().includes(needle) || (a.name || "").toLowerCase().includes(needle))
    : animals;

  const list = document.getElementById("animalList");
  list.innerHTML = "";
  document.getElementById("emptyState").classList.toggle("hidden", animals.length > 0);

  for (const a of filtered.sort((x, y) => x.tag.localeCompare(y.tag))) {
    const card = document.createElement("div");
    card.className = "bg-white rounded-xl p-3 shadow flex items-center justify-between";
    card.innerHTML = `
      <div>
        <div class="font-bold text-lg">${escapeHtml(a.tag)} ${a.name ? `<span class="font-normal text-slate-500">- ${escapeHtml(a.name)}</span>` : ""}</div>
        <div class="text-xs text-slate-500">${escapeHtml(a.breed || "")} ${a.breed ? "&middot;" : ""} ${a.sex === "male" ? "Male" : "Female"}</div>
      </div>
      <span class="text-xs font-semibold px-2 py-1 rounded-full status-pill-${a.status}">${STATUS_LABEL[a.status] || a.status}</span>
    `;
    card.addEventListener("click", () => openAnimalDetail(a.tag));
    list.appendChild(card);
  }
}

function renderSummary() {
  document.getElementById("countAlive").textContent =
    dashboard ? dashboard.total_alive : animals.filter((a) => a.status === "alive").length;
  document.getElementById("countNeedsWeighing").textContent =
    dashboard ? dashboard.needs_weighing.length : "-";
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function eventIcon(kind) {
  return { birth: "fa-baby", weight: "fa-weight-scale", treatment: "fa-syringe",
           movement: "fa-arrows-turn-right", death: "fa-skull", sale: "fa-hand-holding-dollar" }[kind] || "fa-circle";
}

// ---------------------------------------------------------------------
// Loading data
// ---------------------------------------------------------------------

async function loadAnimals() {
  try {
    animals = await Kudde.api("/api/animals");
    Kudde.setOffline(false);
    await IDB.cacheAnimals(animals);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) throw e;
    Kudde.setOffline(true);
    animals = await IDB.getCachedAnimals();
  }
  renderAnimalList();
  renderSummary();
}

async function loadDashboard() {
  try {
    dashboard = await Kudde.api("/api/dashboard");
    Kudde.setOffline(false);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) throw e;
    Kudde.setOffline(true);
    dashboard = null; // no cached copy - the summary falls back to counting `animals`
  }
  renderSummary();
}

// ---------------------------------------------------------------------
// Add animal
// ---------------------------------------------------------------------

function openAddAnimal() {
  for (const id of ["newTag", "newName", "newBreed", "newBirthDate", "newSireTag", "newDamTag"]) {
    document.getElementById(id).value = "";
  }
  document.getElementById("newSex").value = "female";
  document.getElementById("addAnimalModal").classList.remove("hidden");
  document.getElementById("addAnimalModal").classList.add("flex");
}

function closeAddAnimal() {
  document.getElementById("addAnimalModal").classList.add("hidden");
  document.getElementById("addAnimalModal").classList.remove("flex");
}

async function confirmAddAnimal() {
  const tag = document.getElementById("newTag").value.trim();
  if (!tag) { Kudde.toast("Tag number is required"); return; }
  if (animals.some((a) => a.tag === tag)) { Kudde.toast(`Tag ${tag} is already in use`); return; }

  const payload = {
    tag,
    name: document.getElementById("newName").value.trim(),
    breed: document.getElementById("newBreed").value.trim(),
    sex: document.getElementById("newSex").value,
    birth_date: document.getElementById("newBirthDate").value || null,
    sire_tag: document.getElementById("newSireTag").value.trim() || null,
    dam_tag: document.getElementById("newDamTag").value.trim() || null,
  };

  let saved;
  try {
    saved = await Kudde.api("/api/animals", { method: "POST", body: payload });
    Kudde.setOffline(false);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) { Kudde.toast(Kudde.errorDetail(e)); return; }
    Kudde.setOffline(true);
    saved = { ...payload, status: "alive", _pending: true };
    await IDB.enqueue({ uuid: uuid(), kind: "animal", payload });
  }

  animals.push(saved);
  await IDB.cacheAnimals(animals);
  renderAnimalList();
  await loadDashboard();
  closeAddAnimal();
  Kudde.toast(saved._pending ? "Saved on this device - will sync when online" : "Animal added");
}

// ---------------------------------------------------------------------
// Animal detail + events
// ---------------------------------------------------------------------

async function openAnimalDetail(tag) {
  const animal = animals.find((a) => a.tag === tag);
  if (!animal) return;
  activeTag = tag;

  document.getElementById("detailTag").textContent = animal.tag;
  document.getElementById("detailSub").textContent =
    [animal.name, animal.breed, animal.sex === "male" ? "Male" : "Female"].filter(Boolean).join(" · ");
  const pill = document.getElementById("detailStatusPill");
  pill.textContent = STATUS_LABEL[animal.status] || animal.status;
  pill.className = `text-xs font-semibold px-2 py-1 rounded-full status-pill-${animal.status}`;

  const eventsEl = document.getElementById("detailEvents");
  eventsEl.innerHTML = `<div class="text-slate-400">Loading...</div>`;
  document.getElementById("animalDetailModal").classList.remove("hidden");
  document.getElementById("animalDetailModal").classList.add("flex");

  try {
    const events = await Kudde.api(`/api/animals/${encodeURIComponent(tag)}/events`);
    Kudde.setOffline(false);
    eventsEl.innerHTML = events.length
      ? events.map((e) => `
          <div class="flex items-center gap-2 py-1 border-b border-slate-100 last:border-0">
            <i class="fa-solid ${eventIcon(e.kind)} event-icon-${e.kind} w-4"></i>
            <div class="flex-1">
              <span class="font-medium">${e.kind}</span>
              ${e.value != null ? ` - ${e.value}kg` : ""}${e.location ? ` - ${escapeHtml(e.location)}` : ""}
              ${e.note ? `<div class="text-xs text-slate-500">${escapeHtml(e.note)}</div>` : ""}
            </div>
            <div class="text-xs text-slate-400">${Kudde.fmtDate(e.event_date)}</div>
          </div>`).join("")
      : `<div class="text-slate-400">No events recorded yet</div>`;
  } catch (e) {
    if (!Kudde.isNetworkError(e)) throw e;
    Kudde.setOffline(true);
    eventsEl.innerHTML = `<div class="text-slate-400">Offline - history unavailable</div>`;
  }
}

function closeAnimalDetail() {
  document.getElementById("animalDetailModal").classList.add("hidden");
  document.getElementById("animalDetailModal").classList.remove("flex");
  activeTag = null;
}

function openEventModal(kind) {
  activeEventKind = kind;
  const cfg = EVENT_FIELD_CONFIG[kind];
  document.getElementById("eventModalTitle").textContent = cfg.title;
  document.getElementById("eventDate").value = Kudde.localDateStr();
  document.getElementById("eventValue").value = "";
  document.getElementById("eventLocation").value = "";
  document.getElementById("eventNote").value = "";
  document.getElementById("eventValueRow").classList.toggle("hidden", !cfg.showValue);
  if (cfg.showValue) document.getElementById("eventValueLabel").textContent = cfg.valueLabel;
  document.getElementById("eventLocationRow").classList.toggle("hidden", !cfg.showLocation);
  document.getElementById("eventModal").classList.remove("hidden");
  document.getElementById("eventModal").classList.add("flex");
}

function closeEventModal() {
  document.getElementById("eventModal").classList.add("hidden");
  document.getElementById("eventModal").classList.remove("flex");
  activeEventKind = null;
}

const STATUS_ON_EVENT = { death: "dead", sale: "sold" };

async function confirmEvent() {
  const eventDate = document.getElementById("eventDate").value;
  if (!eventDate) { Kudde.toast("Date is required"); return; }

  const payload = {
    tag: activeTag,
    kind: activeEventKind,
    event_date: eventDate,
    value: EVENT_FIELD_CONFIG[activeEventKind].showValue
      ? parseFloat(document.getElementById("eventValue").value) || null : null,
    location: document.getElementById("eventLocation").value.trim(),
    note: document.getElementById("eventNote").value.trim(),
  };

  let pending = false;
  try {
    await Kudde.api("/api/events", { method: "POST", body: payload });
    Kudde.setOffline(false);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) { Kudde.toast(Kudde.errorDetail(e)); return; }
    Kudde.setOffline(true);
    pending = true;
    await IDB.enqueue({ uuid: uuid(), kind: "event", payload });
  }

  // Optimistic local status flip, same rule the server applies - so the list
  // and the detail card read right immediately, online or not.
  const newStatus = STATUS_ON_EVENT[activeEventKind];
  if (newStatus) {
    const animal = animals.find((a) => a.tag === activeTag);
    if (animal) animal.status = newStatus;
    await IDB.cacheAnimals(animals);
  }

  closeEventModal();
  renderAnimalList();
  await loadDashboard();
  Kudde.toast(pending ? "Saved on this device - will sync when online" : "Event recorded");
  if (document.getElementById("animalDetailModal").classList.contains("flex")) {
    openAnimalDetail(activeTag || document.getElementById("detailTag").textContent);
  }
}

// ---------------------------------------------------------------------
// Sync
// ---------------------------------------------------------------------

function updateSyncStatusPill(pendingCount) {
  const el = document.getElementById("syncStatus");
  if (Kudde.isOffline()) {
    el.textContent = pendingCount ? `Offline - ${pendingCount} pending` : "Offline";
  } else {
    el.textContent = pendingCount ? `Syncing ${pendingCount}...` : "Online";
  }
}

async function trySync() {
  const pending = await IDB.getPending();
  updateSyncStatusPill(pending.length);
  if (!pending.length || Kudde.isOffline()) return;

  for (const entry of pending) {
    try {
      if (entry.kind === "animal") {
        await Kudde.api("/api/animals", { method: "POST", body: entry.payload });
      } else if (entry.kind === "event") {
        await Kudde.api("/api/events", { method: "POST", body: entry.payload });
      }
      await IDB.markSynced(entry.uuid);
    } catch (e) {
      if (!Kudde.isNetworkError(e)) {
        // The server rejected it outright (e.g. tag already exists because
        // it synced from another device first) - nothing will change on a
        // retry, so drop it rather than block every entry behind it forever.
        console.warn("[kudde] dropping unsyncable outbox entry", entry, e);
        await IDB.markSynced(entry.uuid);
        continue;
      }
      Kudde.setOffline(true);
      break; // still offline - stop and retry the whole batch next time
    }
  }

  updateSyncStatusPill((await IDB.getPending()).length);
  await loadAnimals();
  await loadDashboard();
}

// ---------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------

async function init() {
  document.getElementById("appVersion").textContent = `v${Kudde.VERSION}`;
  Kudde.offlineBanner("Offline - changes are saved on this device and will sync later");
  Kudde.onOfflineChange = (offline) => { IDB.getPending().then((p) => updateSyncStatusPill(p.length)); };

  document.getElementById("searchInput").addEventListener("input", renderAnimalList);
  document.getElementById("addAnimalBtn").addEventListener("click", openAddAnimal);
  document.getElementById("cancelAddAnimalBtn").addEventListener("click", closeAddAnimal);
  document.getElementById("confirmAddAnimalBtn").addEventListener("click", confirmAddAnimal);
  document.getElementById("closeDetailBtn").addEventListener("click", closeAnimalDetail);
  document.getElementById("cancelEventBtn").addEventListener("click", closeEventModal);
  document.getElementById("confirmEventBtn").addEventListener("click", confirmEvent);

  document.querySelectorAll("#animalDetailModal .action-btn[data-kind]").forEach((btn) => {
    btn.addEventListener("click", () => openEventModal(btn.dataset.kind));
  });

  KWPTR.attach(async () => { await loadAnimals(); await loadDashboard(); await trySync(); });
  window.addEventListener("online", trySync);

  await loadAnimals();
  await loadDashboard();
  await trySync();
  setInterval(trySync, 20000);
}

init().catch((e) => {
  console.error("[kudde] failed to start", e);
  document.body.innerHTML = `<div class="p-6 text-center text-red-600">Failed to start: ${escapeHtml(e.message || e)}</div>`;
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(() => {});
}
