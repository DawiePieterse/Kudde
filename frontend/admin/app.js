// Kudde Admin app. Runs on the farm's office PC on the same local network
// as the server - unlike the field app, there's no offline queue here: if
// the server isn't reachable there's nothing useful for this screen to do
// but say so (see Boord's admin, which works the same way).

const STATUS_LABEL = { alive: "Alive", dead: "Dead", sold: "Sold" };
const EVENT_ICON = { birth: "fa-baby", weight: "fa-weight-scale", treatment: "fa-syringe",
                     movement: "fa-arrows-turn-right", death: "fa-skull", sale: "fa-hand-holding-dollar" };

let animals = [];
let dashboard = null;
let editingTag = null; // null while the modal is adding a new animal

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------

function wireTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.add("hidden"));
      document.getElementById(`tab-${btn.dataset.tab}`).classList.remove("hidden");
    });
  });
}

// ---------------------------------------------------------------------
// Dashboard tab
// ---------------------------------------------------------------------

async function loadDashboard() {
  try {
    dashboard = await Kudde.api("/api/dashboard");
    Kudde.setOffline(false);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) throw e;
    Kudde.setOffline(true);
    return;
  }
  renderDashboard();
}

function renderDashboard() {
  if (!dashboard) return;
  document.getElementById("statAlive").textContent = dashboard.herd_counts.alive;
  document.getElementById("statDead").textContent = dashboard.herd_counts.dead;
  document.getElementById("statSold").textContent = dashboard.herd_counts.sold;

  const needsEl = document.getElementById("needsWeighingList");
  document.getElementById("needsWeighingEmpty").classList.toggle("hidden", dashboard.needs_weighing.length > 0);
  needsEl.innerHTML = dashboard.needs_weighing.map((a) => `
    <div class="flex justify-between items-center py-1 border-b border-slate-100 last:border-0">
      <span><span class="font-semibold">${escapeHtml(a.tag)}</span> ${a.name ? escapeHtml(a.name) : ""}</span>
      <button class="text-xs text-blue-700 underline view-animal-link" data-tag="${escapeHtml(a.tag)}">View</button>
    </div>`).join("");

  const recentEl = document.getElementById("recentActivityList");
  recentEl.innerHTML = dashboard.recent_events.length
    ? dashboard.recent_events.map((e) => `
        <div class="flex items-center gap-2 py-1 border-b border-slate-100 last:border-0">
          <i class="fa-solid ${EVENT_ICON[e.kind] || "fa-circle"} event-icon-${e.kind} w-4"></i>
          <div class="flex-1">${e.kind}${e.value != null ? ` - ${e.value}kg` : ""}${e.location ? ` - ${escapeHtml(e.location)}` : ""}</div>
          <div class="text-xs text-slate-400">${Kudde.fmtDate(e.event_date)}</div>
        </div>`).join("")
    : `<div class="text-slate-400">No activity yet</div>`;

  document.querySelectorAll(".view-animal-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelector('.tab-btn[data-tab="herd"]').click();
      openEditAnimal(btn.dataset.tag);
    });
  });
}

// ---------------------------------------------------------------------
// Herd tab
// ---------------------------------------------------------------------

async function loadAnimals() {
  try {
    animals = await Kudde.api("/api/animals");
    Kudde.setOffline(false);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) throw e;
    Kudde.setOffline(true);
    return;
  }
  renderHerdTable();
}

function renderHerdTable() {
  const needle = document.getElementById("herdSearchInput").value.trim().toLowerCase();
  const statusFilter = document.getElementById("herdStatusFilter").value;
  const filtered = animals.filter((a) => {
    if (statusFilter && a.status !== statusFilter) return false;
    if (!needle) return true;
    return a.tag.toLowerCase().includes(needle) || (a.name || "").toLowerCase().includes(needle);
  }).sort((x, y) => x.tag.localeCompare(y.tag));

  const body = document.getElementById("herdTableBody");
  body.innerHTML = filtered.map((a) => `
    <tr class="border-b last:border-0 hover:bg-slate-50 cursor-pointer" data-tag="${escapeHtml(a.tag)}">
      <td class="p-3 font-semibold">${escapeHtml(a.tag)}</td>
      <td class="p-3">${escapeHtml(a.name || "-")}</td>
      <td class="p-3">${escapeHtml(a.breed || "-")}</td>
      <td class="p-3">${a.sex === "male" ? "Male" : "Female"}</td>
      <td class="p-3">${Kudde.fmtDate(a.birth_date, "-")}</td>
      <td class="p-3"><span class="text-xs font-semibold px-2 py-1 rounded-full status-pill-${a.status}">${STATUS_LABEL[a.status]}</span></td>
    </tr>`).join("");
  document.getElementById("herdEmpty").classList.toggle("hidden", filtered.length > 0);

  body.querySelectorAll("tr").forEach((row) => {
    row.addEventListener("click", () => openEditAnimal(row.dataset.tag));
  });
}

// ---------------------------------------------------------------------
// Add / edit animal modal
// ---------------------------------------------------------------------

function fillForm(a) {
  document.getElementById("fTag").value = a?.tag || "";
  document.getElementById("fTag").disabled = !!a; // the tag is the identity key - not editable once created
  document.getElementById("fName").value = a?.name || "";
  document.getElementById("fSex").value = a?.sex || "female";
  document.getElementById("fBreed").value = a?.breed || "";
  document.getElementById("fBirthDate").value = a?.birth_date || "";
  document.getElementById("fStatus").value = a?.status || "alive";
  document.getElementById("fSireTag").value = a?.sire_tag || "";
  document.getElementById("fDamTag").value = a?.dam_tag || "";
}

function openAddAnimal() {
  editingTag = null;
  fillForm(null);
  document.getElementById("animalModalTitle").textContent = "Add animal";
  document.getElementById("animalModalStatusPill").classList.add("hidden");
  document.getElementById("animalEventsSection").classList.add("hidden");
  showAnimalModal();
}

async function openEditAnimal(tag) {
  const animal = animals.find((a) => a.tag === tag);
  if (!animal) return;
  editingTag = tag;
  fillForm(animal);
  document.getElementById("animalModalTitle").textContent = `Edit ${tag}`;
  const pill = document.getElementById("animalModalStatusPill");
  pill.textContent = STATUS_LABEL[animal.status];
  pill.className = `text-xs font-semibold px-2 py-1 rounded-full status-pill-${animal.status}`;
  document.getElementById("animalEventsSection").classList.remove("hidden");
  document.getElementById("newEventDate").value = Kudde.localDateStr();
  showAnimalModal();
  await refreshEventsList();
}

function showAnimalModal() {
  document.getElementById("animalModal").classList.remove("hidden");
  document.getElementById("animalModal").classList.add("flex");
}

function closeAnimalModal() {
  document.getElementById("animalModal").classList.add("hidden");
  document.getElementById("animalModal").classList.remove("flex");
  document.getElementById("fTag").disabled = false;
  editingTag = null;
}

async function refreshEventsList() {
  const el = document.getElementById("animalEventsList");
  el.innerHTML = `<div class="text-slate-400">Loading...</div>`;
  try {
    const events = await Kudde.api(`/api/animals/${encodeURIComponent(editingTag)}/events`);
    el.innerHTML = events.length
      ? events.map((e) => `
          <div class="flex items-center gap-2 py-1 border-b border-slate-100 last:border-0">
            <i class="fa-solid ${EVENT_ICON[e.kind] || "fa-circle"} event-icon-${e.kind} w-4"></i>
            <div class="flex-1">
              <span class="font-medium">${e.kind}</span>
              ${e.value != null ? ` - ${e.value}kg` : ""}${e.location ? ` - ${escapeHtml(e.location)}` : ""}
              ${e.note ? `<div class="text-xs text-slate-500">${escapeHtml(e.note)}</div>` : ""}
            </div>
            <div class="text-xs text-slate-400">${Kudde.fmtDate(e.event_date)}</div>
          </div>`).join("")
      : `<div class="text-slate-400">No events recorded yet</div>`;
  } catch (e) {
    el.innerHTML = `<div class="text-slate-400">${Kudde.errorDetail(e, "Could not load history")}</div>`;
  }
}

async function saveAnimal() {
  const tag = document.getElementById("fTag").value.trim();
  if (!tag) { Kudde.toast("Tag number is required"); return; }

  const fields = {
    name: document.getElementById("fName").value.trim(),
    breed: document.getElementById("fBreed").value.trim(),
    sex: document.getElementById("fSex").value,
    birth_date: document.getElementById("fBirthDate").value || null,
    sire_tag: document.getElementById("fSireTag").value.trim() || null,
    dam_tag: document.getElementById("fDamTag").value.trim() || null,
  };

  try {
    if (editingTag) {
      fields.status = document.getElementById("fStatus").value;
      await Kudde.api(`/api/animals/${encodeURIComponent(editingTag)}`, { method: "PATCH", body: fields });
    } else {
      if (animals.some((a) => a.tag === tag)) { Kudde.toast(`Tag ${tag} is already in use`); return; }
      await Kudde.api("/api/animals", { method: "POST", body: { tag, ...fields } });
    }
    Kudde.setOffline(false);
  } catch (e) {
    if (Kudde.isNetworkError(e)) { Kudde.setOffline(true); Kudde.toast("Offline - could not save"); return; }
    Kudde.toast(Kudde.errorDetail(e));
    return;
  }

  closeAnimalModal();
  Kudde.toast("Saved");
  await loadAnimals();
  await loadDashboard();
}

async function addEvent() {
  const kind = document.getElementById("newEventKind").value;
  const eventDate = document.getElementById("newEventDate").value;
  if (!eventDate) { Kudde.toast("Date is required"); return; }

  const payload = {
    tag: editingTag,
    kind,
    event_date: eventDate,
    value: kind === "weight" ? (parseFloat(document.getElementById("newEventValue").value) || null) : null,
    location: document.getElementById("newEventLocation").value.trim(),
    note: document.getElementById("newEventNote").value.trim(),
  };

  try {
    await Kudde.api("/api/events", { method: "POST", body: payload });
    Kudde.setOffline(false);
  } catch (e) {
    if (Kudde.isNetworkError(e)) { Kudde.setOffline(true); Kudde.toast("Offline - could not save"); return; }
    Kudde.toast(Kudde.errorDetail(e));
    return;
  }

  document.getElementById("newEventValue").value = "";
  document.getElementById("newEventLocation").value = "";
  document.getElementById("newEventNote").value = "";
  Kudde.toast("Event recorded");
  await refreshEventsList();
  await loadAnimals();   // a sale/death event may have changed this animal's status
  await loadDashboard();
  const refreshed = animals.find((a) => a.tag === editingTag);
  if (refreshed) {
    document.getElementById("fStatus").value = refreshed.status;
    const pill = document.getElementById("animalModalStatusPill");
    pill.textContent = STATUS_LABEL[refreshed.status];
    pill.className = `text-xs font-semibold px-2 py-1 rounded-full status-pill-${refreshed.status}`;
  }
}

// ---------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------

function updateSyncStatusPill() {
  document.getElementById("syncStatus").textContent = Kudde.isOffline() ? "Offline" : "Online";
}

async function init() {
  document.getElementById("appVersion").textContent = `v${Kudde.VERSION}`;
  Kudde.offlineBanner("Offline - the server on this network can't be reached");
  Kudde.onOfflineChange = updateSyncStatusPill;

  wireTabs();
  document.getElementById("herdSearchInput").addEventListener("input", renderHerdTable);
  document.getElementById("herdStatusFilter").addEventListener("change", renderHerdTable);
  document.getElementById("addAnimalBtn").addEventListener("click", openAddAnimal);
  document.getElementById("cancelAnimalBtn").addEventListener("click", closeAnimalModal);
  document.getElementById("saveAnimalBtn").addEventListener("click", saveAnimal);
  document.getElementById("addEventBtn").addEventListener("click", addEvent);

  KWPTR.attach(async () => { await loadAnimals(); await loadDashboard(); });
  window.addEventListener("online", async () => { await loadAnimals(); await loadDashboard(); });

  await loadAnimals();
  await loadDashboard();
  updateSyncStatusPill();
}

init().catch((e) => {
  console.error("[kudde] failed to start", e);
  document.body.innerHTML = `<div class="p-6 text-center text-red-600">Failed to start: ${escapeHtml(e.message || e)}</div>`;
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(() => {});
}
