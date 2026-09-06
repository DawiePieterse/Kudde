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
let farm = null;
let farmMap = null;
let farmMarker = null;

// Center of South Africa - shown until the farm has a saved location, or
// none is set yet. Zoomed out enough that no farm needs to pan far to find
// itself.
const DEFAULT_MAP_CENTER = [-29.0, 24.0];
const DEFAULT_MAP_ZOOM = 5;
const PINNED_MAP_ZOOM = 13;

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// e.g. "18-27°C, 4mm" - blank when the farm has no position set or the
// lookup failed for that day, rather than showing a row of dashes.
function weatherSummary(e) {
  if (e.weather_temp_min == null && e.weather_temp_max == null) return "";
  const temp = e.weather_temp_min != null && e.weather_temp_max != null
    ? `${Math.round(e.weather_temp_min)}-${Math.round(e.weather_temp_max)}°C`
    : `${Math.round(e.weather_temp_min ?? e.weather_temp_max)}°C`;
  const rain = e.weather_precipitation ? `, ${e.weather_precipitation}mm` : "";
  return `${temp}${rain}`;
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
      // Leaflet measures its container on init, so the map can only be
      // created once its tab is actually visible - doing it eagerly on
      // page load gives it a 0x0 box and every tile ends up misplaced.
      if (btn.dataset.tab === "settings") {
        ensureFarmMap();
        farmMap.invalidateSize();
      }
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
          <div class="flex-1">
            <span class="font-semibold">${escapeHtml(e.tag)}</span>${e.name ? ` ${escapeHtml(e.name)}` : ""}
            <span class="text-slate-500">- ${e.kind}${e.value != null ? ` ${e.value}kg` : ""}${e.location ? `${e.kind === "movement" ? " to " : " in "}${escapeHtml(e.location)}` : ""}</span>
          </div>
          <div class="text-xs text-slate-400 text-right">
            <div>${Kudde.fmtDate(e.event_date)}</div>
            ${weatherSummary(e) ? `<div>${weatherSummary(e)}</div>` : ""}
          </div>
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
            <div class="text-xs text-slate-400 text-right">
              <div>${Kudde.fmtDate(e.event_date)}</div>
              ${weatherSummary(e) ? `<div>${weatherSummary(e)}</div>` : ""}
            </div>
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

  const value = kind === "weight"
    ? (parseFloat(document.getElementById("newEventValue").value) || null) : null;
  // See the field app: a blank weight event hides the animal from the one
  // screen meant to catch it. The server refuses it too.
  if (kind === "weight" && !(value > 0)) { Kudde.toast("Enter the weight in kg"); return; }

  const payload = {
    tag: editingTag,
    kind,
    event_date: eventDate,
    value,
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
// Server version / updates
// ---------------------------------------------------------------------

// What the server itself is running, which is not the same question as what
// this browser loaded - a phone or PC holding a cached shell shows an old
// number in its header long after the server moved on. Failures here are
// swallowed on purpose: a farm that cannot answer /api/version still has a
// herd to manage, and this is the least important thing on the screen.
async function loadServerVersion() {
  let info;
  try {
    info = await Kudde.api("/api/version");
  } catch (e) {
    return;
  }

  const versionEl = document.getElementById("appVersion");
  if (info.version && info.version !== Kudde.VERSION) {
    // Deliberately shown rather than reconciled. The two disagreeing is the
    // signal: either this screen is stale (close the app fully and reopen),
    // or the code was checked out without restarting the server.
    versionEl.innerHTML = `v${escapeHtml(Kudde.VERSION)} <span class="text-amber-300">- server v${escapeHtml(info.version)}</span>`;
  }

  const notice = document.getElementById("updateNotice");
  const update = info.update;
  if (!update) return;   // nobody has set up setup_update_check.bat here

  if (update.available) {
    notice.className = "rounded-xl p-3 shadow text-sm bg-amber-50 border border-amber-300 text-amber-900";
    notice.innerHTML = `
      <div class="font-semibold"><i class="fa-solid fa-circle-arrow-up"></i>
        Kudde ${escapeHtml(update.latest || "")} is available</div>
      <div class="mt-1">This server is running ${escapeHtml(update.current || "an untagged checkout")}.
        To install it, double-click <span class="font-mono">update_server.bat</span> in the Kudde
        folder on this PC. It restarts the server, so do it when nobody is recording in the field.</div>`;
    notice.classList.remove("hidden");
  } else if (update.signature && update.signature !== "ok") {
    // A check that has been failing since April looks exactly like "no
    // updates" unless it says so - which is the whole reason the check
    // writes its failures down rather than only its successes.
    notice.className = "rounded-xl p-3 shadow text-sm bg-red-50 border border-red-300 text-red-900";
    notice.innerHTML = `
      <div class="font-semibold"><i class="fa-solid fa-triangle-exclamation"></i>
        The update check could not verify the newest release</div>
      <div class="mt-1">${update.signature === "no-pubkey"
        ? "This PC does not have the Kudde release key, so it cannot tell a genuine release from a tampered one."
        : "The newest release is not signed by the key this server trusts."}
        Nothing has been installed. See UPDATING.md before doing anything by hand.</div>`;
    notice.classList.remove("hidden");
  }
}

// ---------------------------------------------------------------------
// Settings tab
// ---------------------------------------------------------------------

async function loadFarm() {
  try {
    farm = await Kudde.api("/api/farm");
    Kudde.setOffline(false);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) throw e;
    Kudde.setOffline(true);
    return;
  }
  fillFarmForm(farm);
}

function fillFarmForm(f) {
  document.getElementById("fFarmName").value = f?.farm_name || "";
  document.getElementById("fFarmerName").value = f?.farmer_name || "";
  document.getElementById("fPhoneNumber").value = f?.phone_number || "";
  document.getElementById("fGpsLat").value = f?.gps_lat ?? "";
  document.getElementById("fGpsLng").value = f?.gps_lng ?? "";
  if (farmMap) placeMarker(f?.gps_lat, f?.gps_lng, false);
}

// Built the first time the Settings tab is opened (see wireTabs) rather
// than at page load, because Leaflet sizes itself off its container and
// that container is display:none - and therefore 0x0 - until then.
function ensureFarmMap() {
  if (farmMap) return;
  const startLat = farm?.gps_lat ?? DEFAULT_MAP_CENTER[0];
  const startLng = farm?.gps_lng ?? DEFAULT_MAP_CENTER[1];
  const startZoom = (farm?.gps_lat != null) ? PINNED_MAP_ZOOM : DEFAULT_MAP_ZOOM;

  farmMap = L.map("farmMap").setView([startLat, startLng], startZoom);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(farmMap);

  farmMap.on("click", (e) => setGpsFields(e.latlng.lat, e.latlng.lng));

  if (farm?.gps_lat != null && farm?.gps_lng != null) {
    placeMarker(farm.gps_lat, farm.gps_lng, false);
  }
}

// Moves (or creates) the pin. recenter=false is used when re-rendering an
// already-loaded location so opening the tab doesn't yank the view away
// from wherever the farmer last looked.
function placeMarker(lat, lng, recenter) {
  if (lat == null || lng == null) {
    if (farmMarker) { farmMap.removeLayer(farmMarker); farmMarker = null; }
    return;
  }
  if (farmMarker) {
    farmMarker.setLatLng([lat, lng]);
  } else {
    farmMarker = L.marker([lat, lng], { draggable: true }).addTo(farmMap);
    farmMarker.on("dragend", () => {
      const pos = farmMarker.getLatLng();
      setGpsFields(pos.lat, pos.lng, false);
    });
  }
  if (recenter) farmMap.setView([lat, lng], Math.max(farmMap.getZoom(), PINNED_MAP_ZOOM));
}

// Single entry point for "the location changed" - keeps the lat/lng
// inputs and the map pin in sync regardless of whether the change came
// from a map click, a marker drag, geolocation, or typing in the boxes.
function setGpsFields(lat, lng, recenter = true) {
  document.getElementById("fGpsLat").value = lat.toFixed(6);
  document.getElementById("fGpsLng").value = lng.toFixed(6);
  placeMarker(lat, lng, recenter);
}

function useCurrentLocation() {
  if (!navigator.geolocation) { Kudde.toast("Location is not available on this device"); return; }
  navigator.geolocation.getCurrentPosition(
    (pos) => { ensureFarmMap(); setGpsFields(pos.coords.latitude, pos.coords.longitude); },
    () => Kudde.toast("Could not get the current location"),
  );
}

// Typing coordinates directly (rather than using the map or geolocation)
// should still move the pin, once both fields parse to a real number.
function syncMarkerFromInputs() {
  if (!farmMap) return;
  const lat = parseFloat(document.getElementById("fGpsLat").value);
  const lng = parseFloat(document.getElementById("fGpsLng").value);
  if (!isNaN(lat) && !isNaN(lng)) placeMarker(lat, lng, true);
}

async function saveFarm() {
  const lat = document.getElementById("fGpsLat").value;
  const lng = document.getElementById("fGpsLng").value;
  const payload = {
    farm_name: document.getElementById("fFarmName").value.trim(),
    farmer_name: document.getElementById("fFarmerName").value.trim(),
    phone_number: document.getElementById("fPhoneNumber").value.trim(),
    gps_lat: lat === "" ? null : parseFloat(lat),
    gps_lng: lng === "" ? null : parseFloat(lng),
  };

  try {
    farm = await Kudde.api("/api/farm", { method: "PUT", body: payload });
    Kudde.setOffline(false);
  } catch (e) {
    if (Kudde.isNetworkError(e)) { Kudde.setOffline(true); Kudde.toast("Offline - could not save"); return; }
    Kudde.toast(Kudde.errorDetail(e));
    return;
  }

  Kudde.toast("Settings saved");
}

// ---------------------------------------------------------------------
// Move camp (bulk movement)
// ---------------------------------------------------------------------

let moveLocations = [];        // last /api/locations response
let moveSelectedLocation = null;

async function openMoveModal() {
  let data;
  try {
    data = await Kudde.api("/api/locations");
    Kudde.setOffline(false);
  } catch (e) {
    if (Kudde.isNetworkError(e)) { Kudde.setOffline(true); Kudde.toast("Offline - could not load locations"); return; }
    Kudde.toast(Kudde.errorDetail(e));
    return;
  }

  moveLocations = data;
  renderMoveLocations();
  document.getElementById("moveLocationsView").classList.remove("hidden");
  document.getElementById("moveAnimalsView").classList.add("hidden");
  document.getElementById("moveModal").classList.remove("hidden");
  document.getElementById("moveModal").classList.add("flex");
}

function renderMoveLocations() {
  const list = document.getElementById("moveLocationsList");
  document.getElementById("moveLocationsEmpty").classList.toggle("hidden", moveLocations.length > 0);
  list.innerHTML = moveLocations.map((loc) => `
    <button class="w-full text-left bg-slate-50 hover:bg-slate-100 rounded-lg p-3 flex items-center justify-between move-location-btn" data-location="${escapeHtml(loc.location)}">
      <span class="font-semibold">${escapeHtml(loc.location)}</span>
      <span class="text-xs text-slate-500">${loc.animals.length} animal${loc.animals.length === 1 ? "" : "s"}</span>
    </button>`).join("");
  list.querySelectorAll(".move-location-btn").forEach((btn) => {
    btn.addEventListener("click", () => openMoveAnimals(btn.dataset.location));
  });
}

function openMoveAnimals(location) {
  moveSelectedLocation = moveLocations.find((l) => l.location === location);
  if (!moveSelectedLocation) return;

  document.getElementById("moveFromLocation").textContent = location;
  document.getElementById("moveDate").value = Kudde.localDateStr();
  document.getElementById("moveToLocation").value = "";
  document.getElementById("moveNote").value = "";
  renderMoveAnimals();
  document.getElementById("moveLocationsView").classList.add("hidden");
  document.getElementById("moveAnimalsView").classList.remove("hidden");
}

function renderMoveAnimals() {
  document.getElementById("moveAnimalsList").innerHTML = moveSelectedLocation.animals.map((a) => `
    <label class="flex items-center gap-2 py-1">
      <input type="checkbox" class="move-animal-check w-4 h-4" data-tag="${escapeHtml(a.tag)}" checked>
      <span>${escapeHtml(a.tag)}${a.name ? ` - ${escapeHtml(a.name)}` : ""}</span>
    </label>`).join("");
}

function backToMoveLocations() {
  document.getElementById("moveAnimalsView").classList.add("hidden");
  document.getElementById("moveLocationsView").classList.remove("hidden");
  moveSelectedLocation = null;
}

function closeMoveModal() {
  document.getElementById("moveModal").classList.add("hidden");
  document.getElementById("moveModal").classList.remove("flex");
  moveSelectedLocation = null;
}

async function confirmMove() {
  const tags = Array.from(document.querySelectorAll(".move-animal-check:checked")).map((el) => el.dataset.tag);
  if (!tags.length) { Kudde.toast("Select at least one animal"); return; }
  const eventDate = document.getElementById("moveDate").value;
  if (!eventDate) { Kudde.toast("Date is required"); return; }
  const location = document.getElementById("moveToLocation").value.trim();
  if (!location) { Kudde.toast("New location is required"); return; }

  const payload = { tags, event_date: eventDate, location, note: document.getElementById("moveNote").value.trim() };

  try {
    await Kudde.api("/api/events/movement/bulk", { method: "POST", body: payload });
    Kudde.setOffline(false);
  } catch (e) {
    if (Kudde.isNetworkError(e)) { Kudde.setOffline(true); Kudde.toast("Offline - could not save"); return; }
    Kudde.toast(Kudde.errorDetail(e));
    return;
  }

  closeMoveModal();
  Kudde.toast(`Moved ${tags.length} animal${tags.length === 1 ? "" : "s"} to ${location}`);
  await loadAnimals();
  await loadDashboard();
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
  document.getElementById("useMyLocationBtn").addEventListener("click", useCurrentLocation);
  document.getElementById("saveFarmBtn").addEventListener("click", saveFarm);
  document.getElementById("fGpsLat").addEventListener("change", syncMarkerFromInputs);
  document.getElementById("fGpsLng").addEventListener("change", syncMarkerFromInputs);
  document.getElementById("moveCampBtn").addEventListener("click", openMoveModal);
  document.getElementById("cancelMoveBtn").addEventListener("click", closeMoveModal);
  document.getElementById("backMoveBtn").addEventListener("click", backToMoveLocations);
  document.getElementById("confirmMoveBtn").addEventListener("click", confirmMove);

  KWPTR.attach(async () => { await loadAnimals(); await loadDashboard(); });
  window.addEventListener("online", async () => { await loadAnimals(); await loadDashboard(); });

  await loadAnimals();
  await loadDashboard();
  await loadFarm();
  // Last, and not awaited by anything above it: the herd is what this screen
  // is for, and a slow or missing version endpoint must not hold it up.
  loadServerVersion().catch(() => {});
  updateSyncStatusPill();
}

init().catch((e) => {
  console.error("[kudde] failed to start", e);
  document.body.innerHTML = `<div class="p-6 text-center text-red-600">Failed to start: ${escapeHtml(e.message || e)}</div>`;
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(() => {});
}
