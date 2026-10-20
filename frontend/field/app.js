// Kudde Field app. One page, a handful of modals, no framework - matching
// Boord's field/admin apps. Everything works offline: the animal list is
// cached in IndexedDB, and every create/record action either reaches the
// server immediately or is queued in IDB's outbox and replayed once a
// connection comes back (see idb.js and trySync() below).

const EVENT_FIELD_CONFIG = {
  weight: { title: "Record weight", showValue: true, valueLabel: "Weight (kg)", showLocation: false },
  treatment: { title: "Record treatment", showValue: false, showLocation: false, showTreatment: true },
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
let activeTag = null;   // tag shown in the detail modal, for the event modal to act on
let activeEventKind = null;

// Which animals the list shows: "alive" (the default), "all" (dead and sold
// too), or "weigh" (alive and overdue a weighing - the worklist). Remembered
// per device, so the one person who wants the full book on their phone sets
// it once. A search term overrides it: a farmer typing a tag wants that
// animal found whether or not it has left the herd.
const LIST_FILTER_KEY = "kudde_field_list";
let listFilter = "alive";
try {
  const saved = localStorage.getItem(LIST_FILTER_KEY);
  if (["alive", "all", "weigh"].includes(saved)) listFilter = saved;
} catch (_) { /* private mode: default */ }

// Past this many days without a weighing an alive animal needs one. Mirrors
// _STALE_WEIGHT_DAYS in backend/routers/dashboard.py; change both. Worked
// out here, from the cached herd, so the count and the worklist are right
// offline and the moment a weighing is saved - not after the next sync.
const STALE_WEIGHT_DAYS = 182;

function daysSince(dateStr) {
  const d = Kudde._parseDate(dateStr);
  if (!d) return null;
  return Math.floor((Date.now() - d.getTime()) / 86400000);
}

function needsWeighing(a) {
  if (a.status !== "alive") return false;
  const days = daysSince(a.last_weight_date);
  return days === null || days > STALE_WEIGHT_DAYS;
}

// ---------------------------------------------------------------------
// Modals and the back button
// ---------------------------------------------------------------------
//
// Every sheet opens and closes through these two, and each open pushes a
// history entry. On an Android phone the back button then closes the
// sheet instead of leaving the app - which is what it did before, with
// a half-typed weighing on screen. Closing a sheet from its own button
// pops that entry again, so the history never fills with stale ones.
// On iOS there is no back button, and a swipe back behaves the same.

const openModals = [];     // ids, innermost last
let suppressPop = false;   // set while we pop our own entry

function showModal(id) {
  const el = document.getElementById(id);
  el.classList.remove("hidden");
  el.classList.add("flex");
  if (!openModals.includes(id)) {
    openModals.push(id);
    try { history.pushState({ modal: id }, ""); } catch (_) { /* a file:// preview */ }
  }
}

function hideModal(id) {
  const el = document.getElementById(id);
  el.classList.add("hidden");
  el.classList.remove("flex");
  const at = openModals.indexOf(id);
  if (at < 0) return;
  openModals.splice(at, 1);
  if (history.state && history.state.modal === id) {
    suppressPop = true;
    history.back();
  }
}

// The close function for each modal, so the back button closes a sheet
// the same way its own button does - clearing state, not just hiding.
const MODAL_CLOSERS = {
  addAnimalModal: () => closeAddAnimal(),
  animalDetailModal: () => closeAnimalDetail(),
  eventModal: () => closeEventModal(),
  calvingModal: () => closeCalving(),
  moveModal: () => closeMoveModal(),
  sessionModal: () => closeSession(),
  photoViewer: () => closePhotoViewer(),
};

window.addEventListener("popstate", () => {
  if (suppressPop) { suppressPop = false; return; }
  const id = openModals[openModals.length - 1];
  if (!id) return;
  // The entry is already gone, so just hide: hideModal must not go back
  // a second time.
  openModals.pop();
  const closer = MODAL_CLOSERS[id];
  const el = document.getElementById(id);
  el.classList.add("hidden");
  el.classList.remove("flex");
  if (closer) closer();
});

// ---------------------------------------------------------------------
// Date chips: Today / Yesterday above every date box
// ---------------------------------------------------------------------
//
// Nearly every record is today's, the rest are yesterday's written up
// the next morning. Two chips cover both without the native picker,
// which on a phone is a scroll-wheel in the sun. The box stays, for the
// rare older date.

function initDateChips() {
  document.querySelectorAll("[data-date-chips]").forEach((row) => {
    const input = document.getElementById(row.dataset.dateChips);
    const make = (label, daysAgo) => {
      const el = document.createElement("button");
      el.type = "button";
      el.className = "chip";
      el.dataset.daysAgo = daysAgo;
      el.textContent = label;
      el.addEventListener("click", () => {
        const d = new Date();
        d.setDate(d.getDate() - daysAgo);
        setDate(input.id, Kudde.localDateStr(d));
      });
      row.appendChild(el);
    };
    make("Today", 0);
    make("Yesterday", 1);
    input.addEventListener("input", () => syncDateChips(input.id));
  });
}

function syncDateChips(inputId) {
  const row = document.querySelector(`[data-date-chips="${inputId}"]`);
  if (!row) return;
  const value = document.getElementById(inputId).value;
  row.querySelectorAll(".chip").forEach((chip) => {
    const d = new Date();
    d.setDate(d.getDate() - parseInt(chip.dataset.daysAgo, 10));
    chip.classList.toggle("active", value === Kudde.localDateStr(d));
  });
}

// Sets a date box and lights the matching chip. Every place that defaults
// a date to today goes through here.
function setDate(inputId, value) {
  document.getElementById(inputId).value = value;
  syncDateChips(inputId);
}

// ---------------------------------------------------------------------
// Today: what this phone recorded today
// ---------------------------------------------------------------------
//
// The check before leaving the kraal: did the weighing of B12 go in, did
// I record the move. Kept in localStorage rather than asked of the server,
// so it is there with no signal and includes what is still in the outbox;
// a new day starts it empty.

const TODAY_KEY = "kudde_today";

function todayLog() {
  const today = Kudde.localDateStr();
  try {
    const parsed = JSON.parse(localStorage.getItem(TODAY_KEY) || "null");
    if (parsed && parsed.date === today && Array.isArray(parsed.entries)) return parsed;
  } catch (_) { /* unreadable: start afresh */ }
  return { date: today, entries: [] };
}

function logToday(text, tag) {
  const log = todayLog();
  log.entries.unshift({ time: Kudde.fmtTime(new Date()), text, tag: tag || null });
  try { localStorage.setItem(TODAY_KEY, JSON.stringify(log)); } catch (_) { /* full or private: shown this session only */ }
  renderToday(log);
}

function renderToday(log = todayLog()) {
  const n = log.entries.length;
  document.getElementById("todayCard").classList.toggle("hidden", n === 0);
  document.getElementById("todayCount").textContent = n;
  document.getElementById("todayList").innerHTML = log.entries.map((e) => `
    <div class="flex items-center gap-2 py-1 border-b border-slate-100 last:border-0 ${e.tag ? "cursor-pointer" : ""}" ${e.tag ? `data-tag="${escapeHtml(e.tag)}"` : ""}>
      <span class="text-xs text-slate-400 w-11 shrink-0">${escapeHtml(e.time)}</span>
      ${e.tag ? `<span class="font-semibold">${escapeHtml(e.tag)}</span>` : ""}
      <span class="flex-1 min-w-0 truncate">${escapeHtml(e.text)}</span>
    </div>`).join("");
  document.getElementById("todayList").querySelectorAll("[data-tag]").forEach((row) => {
    row.addEventListener("click", () => { if (animals.some((a) => a.tag === row.dataset.tag)) openAnimalDetail(row.dataset.tag); });
  });
}

// One line for the Today list from an event payload.
function describeEvent(payload) {
  const kind = Kudde.eventLabel(payload.kind);
  if (payload.kind === "weight") return `${kind} ${Kudde.fmtKg(payload.value)}`;
  if (payload.kind === "treatment") return payload.product ? `${kind}: ${payload.product}${payload.dose ? ` ${payload.dose}` : ""}` : kind;
  if (payload.kind === "movement") return payload.location ? `Moved to ${payload.location}` : kind;
  return kind;
}

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
    : listFilter === "all" ? animals
    : listFilter === "weigh" ? animals.filter(needsWeighing)
    : animals.filter((a) => a.status === "alive");

  document.getElementById("filterAliveBtn").classList.toggle("active", listFilter === "alive");
  document.getElementById("filterAllBtn").classList.toggle("active", listFilter === "all");
  document.getElementById("needsWeighingBtn").classList.toggle("active", listFilter === "weigh");

  const list = document.getElementById("animalList");
  list.innerHTML = "";
  const empty = document.getElementById("emptyState");
  empty.classList.toggle("hidden", filtered.length > 0);
  // Three different nothings, each with its own way out.
  empty.textContent = !animals.length ? "No animals yet"
    : needle ? "No animal matches"
    : listFilter === "weigh" ? "Nothing needs weighing"
    : "No live animals - tap All to see dead and sold";

  // Sorted on a copy: with no search term `filtered` IS `animals`, and
  // Array.sort works in place - so rendering the list was quietly reordering
  // the module's own copy of the herd.
  for (const a of [...filtered].sort((x, y) => Kudde.compareTags(x.tag, y.tag))) {
    const card = document.createElement("div");
    card.className = "bg-white rounded-xl p-3 shadow flex items-center justify-between";
    card.innerHTML = `
      <div>
        <div class="font-bold text-lg">${escapeHtml(a.tag)} ${a.name ? `<span class="font-normal text-slate-500">- ${escapeHtml(a.name)}</span>` : ""}</div>
        <div class="text-xs text-slate-500">${rowDetail(a)}</div>
      </div>
      <span class="flex items-center gap-1.5 shrink-0">
        ${pendingTags.has(a.tag) ? `<i class="fa-solid fa-cloud-arrow-up unsynced-dot" title="Not yet synced"></i>` : ""}
        <span class="text-xs font-semibold px-2 py-1 rounded-full status-pill-${a.status}">${STATUS_LABEL[a.status] || a.status}</span>
      </span>
    `;
    card.addEventListener("click", () => openAnimalDetail(a.tag));
    list.appendChild(card);
  }
}

// The second line of a row: where the animal is and when it was last
// weighed - what is wanted while walking the camp - rather than the breed
// and sex, which never change and are on the detail card.
function rowDetail(a) {
  const cls = Kudde.classLabel(a.class || Kudde.localClass(a));
  const camp = a.camp ? escapeHtml(a.camp) : "No camp";
  let weighed;
  if (a.last_weight == null) {
    weighed = "Not weighed";
  } else {
    const days = daysSince(a.last_weight_date);
    weighed = `${Kudde.fmtKg(a.last_weight)}${days === null ? "" : ` ${days}d ago`}`;
  }
  const overdue = needsWeighing(a) ? ` <i class="fa-solid fa-triangle-exclamation text-amber-500"></i>` : "";
  const withdrawal = Kudde.inWithdrawal(a.withdrawal_until)
    ? `<div class="mt-0.5"><span class="withdrawal-pill text-xs font-semibold px-2 py-0.5 rounded-full">Withdrawal to ${Kudde.fmtDate(a.withdrawal_until)}</span></div>` : "";
  return `${cls} &middot; ${camp} &middot; ${weighed}${overdue}${withdrawal}`;
}

function renderSummary() {
  document.getElementById("countAlive").textContent = animals.filter((a) => a.status === "alive").length;
  document.getElementById("countNeedsWeighing").textContent = animals.filter(needsWeighing).length;
}

// What to show under a kg box for `animal`, given what has been typed so
// far: the last weighing, the change since, and a nudge when the number
// is a long way off. Returns {text, warn}.
function weightHint(animal, typed) {
  if (!animal) return { text: "", warn: false };
  if (animal.last_weight == null) return { text: "First weighing", warn: false };
  const days = daysSince(animal.last_weight_date);
  let text = `Last ${Kudde.fmtKg(animal.last_weight)} on ${Kudde.fmtDate(animal.last_weight_date)}`
    + (days === null ? "" : ` (${days} day${days === 1 ? "" : "s"} ago)`);
  const value = parseFloat(typed);
  if (!(value > 0)) return { text, warn: false };
  const diff = value - animal.last_weight;
  const pct = Math.abs(diff) / animal.last_weight;
  const sign = diff >= 0 ? "+" : "-";
  text += ` \u00b7 ${sign}${Math.abs(diff).toLocaleString(Kudde.LOCALE, { maximumFractionDigits: 1 })} kg`;
  if (days > 0) text += ` (${sign}${(Math.abs(diff) / days).toLocaleString(Kudde.LOCALE, { maximumFractionDigits: 2 })} kg/day)`;
  // Three tenths either way is beyond any gain or loss a season produces;
  // 412 typed as 41, or the previous animal's weight left in the box, is
  // not. A question, never a refusal: the number may be right.
  if (pct > 0.3) text += ` \u00b7 check: ${Math.round(pct * 100)}% ${diff < 0 ? "lighter" : "heavier"} than last time`;
  return { text, warn: pct > 0.3 };
}

function showWeightHint(el, animal, typed) {
  const { text, warn } = weightHint(animal, typed);
  el.textContent = text;
  el.classList.toggle("hint-warn", warn);
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
  // The camp and product lists ride along with the herd: same moment, same
  // fallback.
  await KuddeCamps.load();
  await KuddeProducts.load();
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

// ---------------------------------------------------------------------
// Add animal
// ---------------------------------------------------------------------

function openAddAnimal() {
  for (const id of ["newTag", "newName", "newBreed", "newBirthDate", "newSireTag", "newDamTag", "newCamp"]) {
    document.getElementById(id).value = "";
  }
  setNewSex(null);
  document.getElementById("newCastrated").checked = false;
  KuddeCamps.render(document.getElementById("newCampChips"), document.getElementById("newCamp"));
  showModal("addAnimalModal");
}

function closeAddAnimal() {
  hideModal("addAnimalModal");
}

let newSex = null;

// No default: a dropdown that opened on Female recorded a bull as a cow
// whenever nobody looked. The steer tick only shows under Male.
function setNewSex(sex) {
  newSex = sex;
  document.getElementById("newFemaleBtn").classList.toggle("active", sex === "female");
  document.getElementById("newMaleBtn").classList.toggle("active", sex === "male");
  document.getElementById("newCastratedRow").classList.toggle("hidden", sex !== "male");
}

async function confirmAddAnimal() {
  const tag = document.getElementById("newTag").value.trim();
  if (!tag) { Kudde.toast("Tag number is required"); return; }
  if (!newSex) { Kudde.toast("Female or male?"); return; }
  if (animals.some((a) => a.tag === tag)) { Kudde.toast(`Tag ${tag} is already in use`); return; }

  const payload = {
    tag,
    name: document.getElementById("newName").value.trim(),
    breed: document.getElementById("newBreed").value.trim(),
    sex: newSex,
    castrated: newSex === "male" && document.getElementById("newCastrated").checked,
    birth_date: document.getElementById("newBirthDate").value || null,
    sire_tag: document.getElementById("newSireTag").value.trim() || null,
    dam_tag: document.getElementById("newDamTag").value.trim() || null,
    camp: document.getElementById("newCamp").value.trim() || null,
  };
  const newCamp = payload.camp && !KuddeCamps.isKnown(payload.camp) ? payload.camp : null;

  let saved;
  try {
    saved = await Kudde.api("/api/animals", { method: "POST", body: payload });
    Kudde.setOffline(false);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) { Kudde.toast(Kudde.errorDetail(e)); return; }
    Kudde.setOffline(true);
    saved = { ...payload, status: "alive", _pending: true };
    await IDB.enqueue({ uuid: uuid(), kind: "animal", payload });
    refreshSyncStatusPill().catch(() => {});
  }

  if (saved._pending && payload.camp) saved.camp = payload.camp;
  logToday(`Added${payload.camp ? ` in ${payload.camp}` : ""}${saved._pending ? " (on phone)" : ""}`, tag);
  animals.push(saved);
  await IDB.cacheAnimals(animals);
  renderAnimalList();
  renderSummary();
  closeAddAnimal();
  Kudde.toast(saved._pending ? "Saved on this device - will sync when online"
    : newCamp ? `Animal added - new camp ${newCamp} added to Settings` : "Animal added");
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
    [animal.name, animal.breed, Kudde.classLabel(animal.class || Kudde.localClass(animal)),
     Kudde.inWithdrawal(animal.withdrawal_until) ? `Withdrawal to ${Kudde.fmtDate(animal.withdrawal_until)}` : null]
      .filter(Boolean).join(" · ");
  document.getElementById("calvedBtn").classList.toggle("hidden", animal.sex !== "female");
  const pill = document.getElementById("detailStatusPill");
  pill.textContent = STATUS_LABEL[animal.status] || animal.status;
  pill.className = `text-xs font-semibold px-2 py-1 rounded-full status-pill-${animal.status}`;

  const eventsEl = document.getElementById("detailEvents");
  eventsEl.innerHTML = `<div class="text-slate-400">Loading...</div>`;
  showModal("animalDetailModal");

  await renderAnimalPhotos();

  try {
    const events = await Kudde.api(`/api/animals/${encodeURIComponent(tag)}/events`);
    Kudde.setOffline(false);
    eventsEl.innerHTML = events.length
      ? events.map((e) => `
          <div class="flex items-center gap-2 py-1 border-b border-slate-100 last:border-0">
            <i class="fa-solid ${eventIcon(e.kind)} event-icon-${e.kind} w-4"></i>
            <div class="flex-1">
              <span class="font-medium">${escapeHtml(Kudde.eventLabel(e.kind))}</span>
              ${e.value != null ? ` - ${Kudde.fmtKg(e.value)}` : ""}${e.location ? ` - ${escapeHtml(e.location)}` : ""}${Kudde.treatmentSummary(e) ? ` - ${escapeHtml(Kudde.treatmentSummary(e))}` : ""}
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

async function renderAnimalPhotos() {
  const el = document.getElementById("detailPhotos");
  el.innerHTML = `<div class="text-slate-400 text-sm">Loading...</div>`;
  try {
    const photos = await Kudde.api(`/api/animals/${encodeURIComponent(activeTag)}/photos`);
    Kudde.setOffline(false);
    el.innerHTML = photos.length ? "" : `<div class="text-slate-400 text-sm">No photos yet</div>`;
    for (const p of photos) {
      const thumb = document.createElement("div");
      thumb.className = "relative shrink-0";
      thumb.innerHTML = `
        <img src="${p.url}" class="w-20 h-20 object-cover rounded-lg border border-slate-200 cursor-pointer">
        <button class="delete-photo-btn absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-black/60 text-white text-xs flex items-center justify-center" data-id="${p.id}">
          <i class="fa-solid fa-xmark"></i>
        </button>`;
      thumb.querySelector("img").addEventListener("click", () => openPhotoViewer(p.url));
      thumb.querySelector(".delete-photo-btn").addEventListener("click", () => deleteAnimalPhoto(p.id));
      el.appendChild(thumb);
    }
  } catch (e) {
    if (!Kudde.isNetworkError(e)) throw e;
    Kudde.setOffline(true);
    el.innerHTML = `<div class="text-slate-400 text-sm">Offline - photos unavailable</div>`;
  }
}

function openPhotoViewer(url) {
  document.getElementById("photoViewerImg").src = url;
  showModal("photoViewer");
}

function closePhotoViewer() {
  hideModal("photoViewer");
  document.getElementById("photoViewerImg").src = "";
}

async function deleteAnimalPhoto(id) {
  // The x on a thumbnail is small and sits right where a thumb lands when
  // scrolling the photo strip, and a deleted photo has no undo.
  if (!await Kudde.confirm({ title: `Delete this photo of ${activeTag}?`, message: "This can't be undone.",
                             confirmLabel: "Delete", danger: true })) return;
  try {
    await Kudde.api(`/api/photos/${id}`, { method: "DELETE" });
    Kudde.setOffline(false);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) { Kudde.toast(Kudde.errorDetail(e)); return; }
    Kudde.setOffline(true);
    Kudde.toast("Offline - can't delete a photo right now");
    return;
  }
  await renderAnimalPhotos();
}

async function uploadAnimalPhoto(tag, file) {
  // Stamped at capture, not at send, and kept for the replay: the server
  // stores a photo once per client_uuid. A several-MB photo over farm wifi
  // routinely outlasts the 8s deadline, and the upload may well have landed
  // anyway - this is the only thing that tells that retry apart from a
  // genuine second picture of the same animal.
  const clientUuid = uuid();
  try {
    await Kudde.apiUpload(`/api/animals/${encodeURIComponent(tag)}/photos`,
                          photoFormData(file, clientUuid));
    Kudde.setOffline(false);
    Kudde.toast("Photo saved");
    logToday("Photo", tag);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) { Kudde.toast(Kudde.errorDetail(e)); return; }
    Kudde.setOffline(true);
    await IDB.enqueue({ uuid: clientUuid, kind: "photo",
                        payload: { tag, blob: file, type: file.type, client_uuid: clientUuid } });
    Kudde.toast("Saved on this device - will sync when online");
    logToday("Photo (on phone)", tag);
  }
  if (activeTag === tag) await renderAnimalPhotos();
}

// The upload body, built the same way whether it is being sent now or
// replayed out of the outbox - the client_uuid has to survive that round
// trip, which is the whole point of it.
function photoFormData(blob, clientUuid, type) {
  const formData = new FormData();
  formData.append("file", blob, `photo.${(type || blob.type || "image/jpeg").split("/")[1] || "jpg"}`);
  formData.append("client_uuid", clientUuid);
  return formData;
}

function closeAnimalDetail() {
  hideModal("animalDetailModal");
  activeTag = null;
}

function openEventModal(kind) {
  activeEventKind = kind;
  const cfg = EVENT_FIELD_CONFIG[kind];
  document.getElementById("eventModalTitle").textContent = cfg.title;
  setDate("eventDate", Kudde.localDateStr());
  document.getElementById("eventValue").value = "";
  document.getElementById("eventLocation").value = "";
  document.getElementById("eventNote").value = "";
  document.getElementById("eventValueRow").classList.toggle("hidden", !cfg.showValue);
  if (cfg.showValue) {
    document.getElementById("eventValueLabel").textContent = cfg.valueLabel;
    showWeightHint(document.getElementById("eventValueHint"), animals.find((a) => a.tag === activeTag), "");
  }
  document.getElementById("eventTreatmentRows").classList.toggle("hidden", !cfg.showTreatment);
  if (cfg.showTreatment) {
    for (const id of ["eventProduct", "eventDose", "eventWithdrawal"]) document.getElementById(id).value = "";
    KuddeProducts.render(document.getElementById("eventProductChips"), {
      product: document.getElementById("eventProduct"), dose: document.getElementById("eventDose"),
      withdrawal: document.getElementById("eventWithdrawal") });
  }
  document.getElementById("eventLocationRow").classList.toggle("hidden", !cfg.showLocation);
  if (cfg.showLocation) {
    KuddeCamps.render(document.getElementById("eventLocationChips"), document.getElementById("eventLocation"));
  }
  showModal("eventModal");
}

function closeEventModal() {
  hideModal("eventModal");
  activeEventKind = null;
}

const STATUS_ON_EVENT = { death: "dead", sale: "sold" };

// Records one event, now if the server answers and into the outbox if not,
// and brings the cached animal into line with it. Shared by the event
// modal and the sessions, so there is one place that knows the rules.
// Returns {pending} or null if the server refused it (already toasted).
// event_date plus the withdrawal, as YYYY-MM-DD - the same sum the server
// makes, done here so the list flags the animal before the next sync.
function withdrawalEnd(eventDate, days) {
  const d = Kudde._parseDate(eventDate);
  d.setDate(d.getDate() + days);
  return Kudde.localDateStr(d);
}

// What the sheet before a sale has to say if the animal is still in
// withdrawal: the one thing on this screen that can cost the farm its
// buyer. A warning, not a refusal - the sale may be to another farm.
function saleWarning(animal) {
  return animal && Kudde.inWithdrawal(animal.withdrawal_until)
    ? `${animal.tag} is in withdrawal until ${Kudde.fmtDate(animal.withdrawal_until)} and should not be slaughtered before then.`
    : "";
}

async function saveEvent(fields) {
  const payload = {
    ...fields,
    // Stamped at capture, not at send, and kept for the replay: the server
    // records an event once per client_uuid. A request that times out (8s)
    // may well have landed, and this is the only thing that can tell the
    // retry apart from a genuine second event - the same animal really can
    // be treated twice on one day.
    client_uuid: uuid(),
  };

  let pending = false;
  try {
    await Kudde.api("/api/events", { method: "POST", body: payload });
    Kudde.setOffline(false);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) { Kudde.toast(Kudde.errorDetail(e)); return null; }
    Kudde.setOffline(true);
    pending = true;
    await IDB.enqueue({ uuid: payload.client_uuid, kind: "event", payload });
    refreshSyncStatusPill().catch(() => {});
  }

  // The cached animal follows the event straight away, by the same rules
  // the server applies, so the list, the counts and the next hint read
  // right immediately, online or not: a death or sale flips the status, a
  // weighing becomes the last weight, a move or birth sets the camp.
  const animal = animals.find((a) => a.tag === payload.tag);
  if (animal) {
    const newStatus = STATUS_ON_EVENT[payload.kind];
    if (newStatus) animal.status = newStatus;
    if (payload.kind === "weight" && payload.value > 0
        && (!animal.last_weight_date || payload.event_date >= animal.last_weight_date)) {
      animal.last_weight = payload.value;
      animal.last_weight_date = payload.event_date;
    }
    if ((payload.kind === "movement" || payload.kind === "birth") && payload.location) {
      animal.camp = payload.location;
    }
    if (payload.kind === "treatment" && payload.withdrawal_days != null) {
      const until = withdrawalEnd(payload.event_date, payload.withdrawal_days);
      if (!animal.withdrawal_until || until > animal.withdrawal_until) animal.withdrawal_until = until;
    }
    await IDB.cacheAnimals(animals);
  }
  if (payload.kind === "treatment") {
    KuddeProducts.remember({ product: payload.product, dose: payload.dose, withdrawal_days: payload.withdrawal_days });
  }
  logToday(describeEvent(payload) + (pending ? " (on phone)" : ""), payload.tag);
  return { pending };
}

// The three treatment boxes as the server wants them. A blank withdrawal
// is null (none recorded), not zero - zero means "may be sold today".
function treatmentFields(productId, doseId, withdrawalId) {
  const raw = document.getElementById(withdrawalId).value.trim();
  const days = /^\d+$/.test(raw) ? parseInt(raw, 10) : null;
  return {
    product: document.getElementById(productId).value.trim(),
    dose: document.getElementById(doseId).value.trim(),
    withdrawal_days: days,
  };
}

async function confirmEvent() {
  const eventDate = document.getElementById("eventDate").value;
  if (!eventDate) { Kudde.toast("Date is required"); return; }

  // Death and Sale take the animal out of the live herd (the server flips
  // its status), and the two buttons sit beside Birth and Move in the same
  // grid - easy to hit with a gloved thumb. Asked here, at Save, rather than
  // on opening the modal, so it names exactly what is about to be recorded.
  if (STATUS_ON_EVENT[activeEventKind]) {
    const what = activeEventKind === "death"
      ? `Record ${activeTag} as dead`
      : `Record ${activeTag} as sold`;
    const warning = activeEventKind === "sale" ? saleWarning(animals.find((a) => a.tag === activeTag)) : "";
    const ok = await Kudde.confirm({
      title: `${what}?`,
      message: `It will be marked ${STATUS_LABEL[STATUS_ON_EVENT[activeEventKind]]} and leave the live herd.`,
      warning, danger: true,
      confirmLabel: activeEventKind === "death" ? "Record death" : "Record sale",
    });
    if (!ok) return;
  }

  const value = EVENT_FIELD_CONFIG[activeEventKind].showValue
    ? parseFloat(document.getElementById("eventValue").value) || null : null;
  // A weight event with no weight in it silences the "needs weighing"
  // reminder for another six months while recording nothing, so the server
  // now refuses one. Caught here as well because this modal is the one place
  // it can be typed, and the offline path never reaches the server to be
  // told: an empty weight would sit in the outbox and be dropped on sync.
  if (activeEventKind === "weight" && !(value > 0)) {
    Kudde.toast("Enter the weight in kg");
    return;
  }

  const payload = {
    tag: activeTag,
    kind: activeEventKind,
    event_date: eventDate,
    value,
    location: document.getElementById("eventLocation").value.trim(),
    note: document.getElementById("eventNote").value.trim(),
    ...treatmentFields("eventProduct", "eventDose", "eventWithdrawal"),
  };
  if (activeEventKind === "treatment" && payload.withdrawal_days === null
      && document.getElementById("eventWithdrawal").value.trim() !== "") {
    Kudde.toast("Withdrawal must be a whole number of days");
    return;
  }

  const result = await saveEvent(payload);
  if (!result) return;
  const { pending } = result;

  const newCamp = payload.location && !KuddeCamps.isKnown(payload.location) ? payload.location : null;
  closeEventModal();
  renderAnimalList();
  renderSummary();
  if (newCamp && !pending) await KuddeCamps.load();
  Kudde.toast(pending ? "Saved on this device - will sync when online"
    : newCamp ? `Event recorded - new camp ${newCamp} added to Settings` : "Event recorded");
  if (document.getElementById("animalDetailModal").classList.contains("flex")) {
    openAnimalDetail(activeTag || document.getElementById("detailTag").textContent);
  }
}

// ---------------------------------------------------------------------
// Calving: the cow calved, and here is the calf
// ---------------------------------------------------------------------
//
// One screen for what used to be two forms and a retyped dam tag: a birth
// event on the cow (camp, and in the note how it went) and the new calf
// with her as dam, her breed, the date as birth date and the same camp.
// The server records both or neither (routers/calvings.py); offline it is
// one outbox entry, replayed as one.

let calfSex = "female";

function setCalfSex(sex) {
  calfSex = sex;
  document.getElementById("calfFemaleBtn").classList.toggle("active", sex === "female");
  document.getElementById("calfMaleBtn").classList.toggle("active", sex === "male");
}

function openCalving() {
  const dam = animals.find((a) => a.tag === activeTag);
  if (!dam) return;
  document.getElementById("calvingDam").textContent = dam.tag;
  setDate("calvingDate", Kudde.localDateStr());
  for (const id of ["calfTag", "calfName", "calfSireTag", "calvingNote"]) document.getElementById(id).value = "";
  // The cow's breed and camp are the calf's until told otherwise.
  document.getElementById("calfBreed").value = dam.breed || "";
  document.getElementById("calvingCamp").value = dam.camp || "";
  KuddeCamps.render(document.getElementById("calvingCampChips"), document.getElementById("calvingCamp"));
  // No default sex: the two buttons start unlit, so the choice is made,
  // not inherited from whichever came first in the markup.
  calfSex = null;
  document.getElementById("calfFemaleBtn").classList.remove("active");
  document.getElementById("calfMaleBtn").classList.remove("active");
  showModal("calvingModal");
  document.getElementById("calfTag").focus();
}

function closeCalving() {
  hideModal("calvingModal");
}

async function confirmCalving() {
  const dam = animals.find((a) => a.tag === activeTag);
  if (!dam) return;
  const eventDate = document.getElementById("calvingDate").value;
  if (!eventDate) { Kudde.toast("Date is required"); return; }
  const calfTag = document.getElementById("calfTag").value.trim();
  if (!calfTag) { Kudde.toast("The calf needs a tag number"); return; }
  if (animals.some((a) => a.tag.toLowerCase() === calfTag.toLowerCase())) { Kudde.toast(`Tag ${calfTag} is already in use`); return; }
  if (!calfSex) { Kudde.toast("Heifer or bull calf?"); return; }

  const camp = document.getElementById("calvingCamp").value.trim();
  const payload = {
    dam_tag: dam.tag,
    event_date: eventDate,
    calf: {
      tag: calfTag, sex: calfSex,
      name: document.getElementById("calfName").value.trim(),
      breed: document.getElementById("calfBreed").value.trim(),
      sire_tag: document.getElementById("calfSireTag").value.trim() || null,
    },
    camp,
    note: document.getElementById("calvingNote").value.trim(),
    client_uuid: uuid(),   // see saveEvent: what makes a replay record once
  };

  let calf;
  let pending = false;
  try {
    calf = (await Kudde.api("/api/calvings", { method: "POST", body: payload })).calf;
    Kudde.setOffline(false);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) { Kudde.toast(Kudde.errorDetail(e)); return; }
    Kudde.setOffline(true);
    pending = true;
    await IDB.enqueue({ uuid: payload.client_uuid, kind: "calving", payload });
    refreshSyncStatusPill().catch(() => {});
    calf = { ...payload.calf, dam_tag: dam.tag, birth_date: eventDate, status: "alive",
             camp: camp || null, last_weight: null, last_weight_date: null, _pending: true };
  }
  if (calf) {
    calf.camp = calf.camp || camp || null;
    animals.push(calf);
    await IDB.cacheAnimals(animals);
  }
  logToday(`Calved, calf ${calfTag}${pending ? " (on phone)" : ""}`, dam.tag);
  const newCamp = camp && !KuddeCamps.isKnown(camp) ? camp : null;
  closeCalving();
  renderAnimalList();
  renderSummary();
  if (newCamp && !pending) await KuddeCamps.load();
  Kudde.toast(pending ? `Saved on this device - calf ${calfTag} will sync when online`
    : `Calf ${calfTag} added to ${dam.tag}` + (newCamp ? " (new camp added to Settings)" : ""));
  openAnimalDetail(dam.tag);
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
    if (!Kudde.isNetworkError(e)) throw e;
    Kudde.setOffline(true);
    Kudde.toast("Offline - connect to move a camp");
    return;
  }

  moveLocations = data;
  renderMoveLocations();
  document.getElementById("moveLocationsView").classList.remove("hidden");
  document.getElementById("moveAnimalsView").classList.add("hidden");
  showModal("moveModal");
}

function renderMoveLocations() {
  const list = document.getElementById("moveLocationsList");
  document.getElementById("moveLocationsEmpty").classList.toggle("hidden", moveLocations.length > 0);
  // Every camp, empty ones included, so the list reads as the whole farm.
  // An empty camp cannot be moved FROM, so it is shown greyed rather than
  // left out. The row with no location is the animals that have never
  // been placed anywhere - the way to move them INTO a camp.
  list.innerHTML = moveLocations.map((loc, i) => {
    const n = loc.animals.length;
    const label = loc.location === null ? "No camp yet" : loc.location;
    return `
    <button class="w-full text-left rounded-lg p-3 flex items-center justify-between move-location-btn ${n ? "bg-slate-50 hover:bg-slate-100" : "bg-slate-50 opacity-50"}"
            data-index="${i}" ${n ? "" : "disabled"}>
      <span class="font-semibold ${loc.location === null ? "italic" : ""}">${escapeHtml(label)}</span>
      <span class="text-xs text-slate-500">${n ? `${n} animal${n === 1 ? "" : "s"}` : "empty"}</span>
    </button>`;
  }).join("");
  list.querySelectorAll(".move-location-btn").forEach((btn) => {
    btn.addEventListener("click", () => openMoveAnimals(parseInt(btn.dataset.index, 10)));
  });
}

function openMoveAnimals(index) {
  moveSelectedLocation = moveLocations[index];
  if (!moveSelectedLocation) return;

  document.getElementById("moveFromLocation").textContent = moveSelectedLocation.location === null
    ? "no camp" : moveSelectedLocation.location;
  setDate("moveDate", Kudde.localDateStr());
  document.getElementById("moveToLocation").value = "";
  document.getElementById("moveNote").value = "";
  KuddeCamps.render(document.getElementById("moveToChips"), document.getElementById("moveToLocation"),
                    { exclude: moveSelectedLocation.location });
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
  hideModal("moveModal");
  moveSelectedLocation = null;
}

async function confirmMove() {
  const tags = Array.from(document.querySelectorAll(".move-animal-check:checked")).map((el) => el.dataset.tag);
  if (!tags.length) { Kudde.toast("Select at least one animal"); return; }
  const eventDate = document.getElementById("moveDate").value;
  if (!eventDate) { Kudde.toast("Date is required"); return; }
  const location = document.getElementById("moveToLocation").value.trim();
  if (!location) { Kudde.toast("Pick the camp they are moving to"); return; }
  const newCamp = KuddeCamps.isKnown(location) ? null : location;

  const payload = {
    tags, event_date: eventDate, location,
    note: document.getElementById("moveNote").value.trim(),
    // Same contract as a single event: stamped at capture, kept for the
    // replay, and what stops a timed-out-but-landed camp move from moving
    // the whole camp a second time when the outbox retries it.
    client_uuid: uuid(),
  };

  let pending = false;
  try {
    await Kudde.api("/api/events/movement/bulk", { method: "POST", body: payload });
    Kudde.setOffline(false);
  } catch (e) {
    if (!Kudde.isNetworkError(e)) { Kudde.toast(Kudde.errorDetail(e)); return; }
    Kudde.setOffline(true);
    pending = true;
    await IDB.enqueue({ uuid: payload.client_uuid, kind: "bulk_movement", payload });
    refreshSyncStatusPill().catch(() => {});
  }

  logToday(`Moved ${tags.length} animal${tags.length === 1 ? "" : "s"} to ${location}${pending ? " (on phone)" : ""}`, null);
  // The cached herd follows the move at once; loadAnimals then replaces it
  // with the server's copy when there is one.
  for (const a of animals) if (tags.includes(a.tag)) a.camp = location;
  await IDB.cacheAnimals(animals);
  closeMoveModal();
  await loadAnimals();   // reloads the camp list too, so a new camp has its chip next time
  Kudde.toast(pending
    ? `Saved on this device - will sync ${tags.length} moves when online`
    : `Moved ${tags.length} animal${tags.length === 1 ? "" : "s"} to ${location}`
      + (newCamp ? " (new camp added to Settings)" : ""));
}

// ---------------------------------------------------------------------
// Sessions: weigh or treat one animal after another
// ---------------------------------------------------------------------
//
// A weighing day is forty head through a crush, and the one-animal flow
// (search, tap, Weight, type, Save, Close) is six steps a head. A session
// is two: tag, number, Save and next. Each save is an ordinary event
// through saveEvent(), so it lands or queues exactly as a single one does
// and nothing new is needed on the server or in the outbox.

let session = null;   // {kind, date, note, log: [{tag, value, pending}]} while one is open

function openSessionSetup(kind) {
  session = { kind, date: Kudde.localDateStr(), note: "", treatment: null, log: [] };
  document.getElementById("sessionSetupTitle").textContent = kind === "weight" ? "Weigh session" : "Treat session";
  setDate("sessionDate", session.date);
  for (const id of ["sessionNote", "sessionProduct", "sessionDose", "sessionWithdrawal"]) document.getElementById(id).value = "";
  document.getElementById("sessionTreatmentRows").classList.toggle("hidden", kind !== "treatment");
  if (kind === "treatment") {
    KuddeProducts.render(document.getElementById("sessionProductChips"), {
      product: document.getElementById("sessionProduct"), dose: document.getElementById("sessionDose"),
      withdrawal: document.getElementById("sessionWithdrawal") });
  }
  document.getElementById("sessionSetupView").classList.remove("hidden");
  document.getElementById("sessionCaptureView").classList.add("hidden");
  showModal("sessionModal");
}

function startSession() {
  const date = document.getElementById("sessionDate").value;
  if (!date) { Kudde.toast("Date is required"); return; }
  session.date = date;
  session.note = document.getElementById("sessionNote").value.trim();
  if (session.kind === "treatment") {
    session.treatment = treatmentFields("sessionProduct", "sessionDose", "sessionWithdrawal");
    if (!session.treatment.product) { Kudde.toast("Say what the product is"); return; }
    if (session.treatment.withdrawal_days === null && document.getElementById("sessionWithdrawal").value.trim() !== "") {
      Kudde.toast("Withdrawal must be a whole number of days"); return;
    }
  }

  document.getElementById("sessionTitle").textContent = session.kind === "weight" ? "Weighing"
    : `Treating: ${Kudde.treatmentSummary({ kind: "treatment", ...session.treatment })}`;
  document.getElementById("sessionValueRow").classList.toggle("hidden", session.kind !== "weight");
  document.getElementById("sessionSaveBtn").textContent = session.kind === "weight" ? "Save and next" : "Record and next";
  // Every alive tag, so the tag box completes after a character or two.
  document.getElementById("tagList").innerHTML = animals
    .filter((a) => a.status === "alive")
    .map((a) => `<option value="${escapeHtml(a.tag)}"></option>`).join("");
  document.getElementById("sessionSetupView").classList.add("hidden");
  document.getElementById("sessionCaptureView").classList.remove("hidden");
  resetSessionEntry();
  renderSessionLog();
}

function closeSession() {
  hideModal("sessionModal");
  const n = session ? session.log.length : 0;
  session = null;
  renderAnimalList();
  renderSummary();
  if (n) Kudde.toast(`Session done - ${n} recorded`);
}

function resetSessionEntry() {
  document.getElementById("sessionTag").value = "";
  document.getElementById("sessionValue").value = "";
  document.getElementById("sessionAnimal").textContent = "";
  document.getElementById("sessionValueHint").textContent = "";
  document.getElementById("sessionTag").focus();
}

// The animal the tag box names, ignoring case, or null.
function sessionAnimal() {
  const typed = document.getElementById("sessionTag").value.trim().toLowerCase();
  if (!typed) return null;
  return animals.find((a) => a.tag.toLowerCase() === typed) || null;
}

function syncSessionEntry() {
  const animal = sessionAnimal();
  const who = document.getElementById("sessionAnimal");
  if (!animal) {
    who.textContent = document.getElementById("sessionTag").value.trim() ? "No animal with that tag" : "";
  } else {
    who.textContent = [animal.name, animal.camp, animal.status !== "alive" ? STATUS_LABEL[animal.status] : null,
                       Kudde.inWithdrawal(animal.withdrawal_until) ? `in withdrawal to ${Kudde.fmtDate(animal.withdrawal_until)}` : null]
      .filter(Boolean).join(" \u00b7 ");
  }
  if (session.kind === "weight") {
    showWeightHint(document.getElementById("sessionValueHint"), animal, document.getElementById("sessionValue").value);
  }
}

async function saveSessionEntry() {
  const animal = sessionAnimal();
  if (!animal) { Kudde.toast("No animal with that tag"); document.getElementById("sessionTag").focus(); return; }
  let value = null;
  if (session.kind === "weight") {
    value = parseFloat(document.getElementById("sessionValue").value) || null;
    if (!(value > 0)) { Kudde.toast("Enter the weight in kg"); document.getElementById("sessionValue").focus(); return; }
  }
  // The same tag twice in one session is usually the previous animal still
  // in the box, not a second weighing - asked, not refused.
  const before = session.log.find((e) => e.tag === animal.tag);
  if (before && !await Kudde.confirm({
    title: `${animal.tag} is already in this session`,
    message: before.value != null ? `Recorded at ${Kudde.fmtKg(before.value)}. Record it again?` : "Record it again?",
    confirmLabel: "Record again" })) return;

  const result = await saveEvent({
    tag: animal.tag, kind: session.kind, event_date: session.date,
    value, location: "", note: session.note, ...(session.treatment || {}),
  });
  if (!result) return;
  session.log.unshift({ tag: animal.tag, value, pending: result.pending });
  renderSessionLog();
  resetSessionEntry();
  refreshSyncStatusPill().catch(() => {});
}

function renderSessionLog() {
  const n = session.log.length;
  document.getElementById("sessionSub").textContent = `${n} recorded`;
  document.getElementById("sessionLog").innerHTML = n ? session.log.map((e) => `
    <div class="flex items-center justify-between py-1 border-b border-slate-100 last:border-0">
      <span class="font-semibold">${escapeHtml(e.tag)}</span>
      <span>${e.value != null ? Kudde.fmtKg(e.value) : ""}</span>
      <span class="text-xs ${e.pending ? "text-amber-600" : "text-slate-400"}">${e.pending ? "saved on phone" : "saved"}</span>
    </div>`).join("") : `<div class="text-slate-400">Nothing yet</div>`;
}

// ---------------------------------------------------------------------
// Sync
// ---------------------------------------------------------------------

let failedCount = 0;   // last known number of outbox entries the server refused
let pendingTags = new Set();   // tags with something of their own still in the outbox

// Which animals an outbox entry is about, for the marker on their rows.
function outboxTags(entry) {
  const p = entry.payload || {};
  if (entry.kind === "bulk_movement") return p.tags || [];
  if (entry.kind === "calving") return [p.dam_tag, p.calf && p.calf.tag].filter(Boolean);
  return p.tag ? [p.tag] : [];
}

function updateSyncStatusPill(pendingCount) {
  const el = document.getElementById("syncStatus");
  let text;
  if (Kudde.isOffline()) {
    text = pendingCount ? `Offline - ${pendingCount} pending` : "Offline";
  } else {
    text = pendingCount ? `Syncing ${pendingCount}...` : "Online";
  }
  if (failedCount) text += ` - ${failedCount} failed`;
  el.textContent = text;
  // Red while anything is stuck, so it is noticed at a glance from the
  // home screen rather than only by someone who thinks to tap the pill.
  el.classList.toggle("bg-red-600", failedCount > 0);
  el.classList.toggle("bg-white/20", failedCount === 0);
}

// Re-reads both counts from IndexedDB and redraws the pill. The failed
// count is kept in a module variable so updateSyncStatusPill can still be
// called synchronously with only the pending count.
async function refreshSyncStatusPill() {
  const [pending, failed] = await Promise.all([IDB.getPending(), IDB.getFailed()]);
  failedCount = failed.length;
  updateSyncStatusPill(pending.length);
  // The rows' unsynced markers follow the outbox: redrawn only when the
  // set of tags changes, so the 20s tick does not repaint a list the
  // user is scrolling.
  const tags = new Set([...pending, ...failed].flatMap(outboxTags));
  const same = tags.size === pendingTags.size && [...tags].every((t) => pendingTags.has(t));
  if (!same) { pendingTags = tags; renderAnimalList(); }
}

// One line naming what a stuck outbox entry was, in the user's terms - the
// tag and the kind of record, not a uuid.
function describeOutboxEntry(entry) {
  const p = entry.payload || {};
  if (entry.kind === "animal") return `New animal ${p.tag}`;
  if (entry.kind === "event") return `${Kudde.eventLabel(p.kind)} for ${p.tag} on ${Kudde.fmtDate(p.event_date)}`;
  if (entry.kind === "bulk_movement") {
    const n = (p.tags || []).length;
    return `Move of ${n} animal${n === 1 ? "" : "s"} to ${p.location}`;
  }
  if (entry.kind === "photo") return `Photo of ${p.tag}`;
  if (entry.kind === "calving") return `Calving of ${p.dam_tag} (calf ${p.calf && p.calf.tag})`;
  return "A saved change";
}

// Tapping the pill walks through each refused entry with the server's own
// reason and asks, one by one, whether to throw it away. Keep is the way
// out (button, backdrop, Escape), so a user who just wants to read the
// reasons loses nothing.
async function reviewFailedEntries() {
  const failed = await IDB.getFailed();
  if (!failed.length) {
    Kudde.toast(Kudde.isOffline() ? "Offline - changes will sync later" : "Everything on this device has synced");
    return;
  }
  for (const entry of failed) {
    const discard = await Kudde.confirm({
      title: `${describeOutboxEntry(entry)} was not saved`,
      message: `The server said: ${entry.error || "no reason given"}\nDiscard it from this device, or keep it here to look at later.`,
      confirmLabel: "Discard", cancelLabel: "Keep", danger: true,
    });
    if (discard) await IDB.discard(entry.uuid);
  }
  await refreshSyncStatusPill();
}

// trySync is called from four places - startup, the 20s interval, the
// browser's "online" event and pull-to-refresh - and two of those routinely
// fire together the moment a device comes back into signal. Two overlapping
// runs read the same outbox and post every entry in it twice; the second
// copy of an animal is caught by the unique tag, but nothing distinguishes a
// duplicated event, so the herd would gain a second identical weighing.
// Observed as a 409 in a browser run before this guard existed. Boord's
// field app carries the same one (syncLoop: "interval + online event + PTR
// can overlap; never double-post").
//
// The guard is belt to the server's braces, not a replacement for them: it
// cannot help across a reload or a second tab, which is what client_uuid is
// for.
let syncBusy = false;

async function trySync() {
  if (syncBusy) return;
  syncBusy = true;
  try {
    const pending = await IDB.getPending();
    failedCount = (await IDB.getFailed()).length;
    updateSyncStatusPill(pending.length);
    if (!pending.length || Kudde.isOffline()) return;

    for (const entry of pending) {
      try {
        if (entry.kind === "animal") {
          await Kudde.api("/api/animals", { method: "POST", body: entry.payload });
        } else if (entry.kind === "event") {
          await Kudde.api("/api/events", { method: "POST", body: entry.payload });
        } else if (entry.kind === "bulk_movement") {
          await Kudde.api("/api/events/movement/bulk", { method: "POST", body: entry.payload });
        } else if (entry.kind === "calving") {
          await Kudde.api("/api/calvings", { method: "POST", body: entry.payload });
        } else if (entry.kind === "photo") {
          await Kudde.apiUpload(
            `/api/animals/${encodeURIComponent(entry.payload.tag)}/photos`,
            photoFormData(entry.payload.blob, entry.payload.client_uuid || entry.uuid, entry.payload.type));
        }
        await IDB.markSynced(entry.uuid);
      } catch (e) {
        if (!Kudde.isNetworkError(e)) {
          const status = parseInt(String(e && e.message || "").slice(0, 3), 10);
          // 403 is about this DEVICE, not this entry: the request came in
          // over the farm wifi rather than Tailscale (security.py), and every
          // entry would get the same answer. Marking them failed would bury a
          // whole day's records behind a routing problem, so stop and retry
          // the batch later, exactly as if the server were out of reach.
          if (status === 403) break;
          // A 5xx is the server's own trouble and may well pass (a restart,
          // a locked database). Leave it queued for the next pass, but move
          // on so it doesn't hold up every entry behind it.
          if (!(status >= 400 && status < 500)) continue;
          // Any other 4xx is the server refusing this record outright (the
          // tag was already added from another device, the animal it names
          // was never created). A retry will get the same answer, so it
          // stops being sent - but it is KEPT, marked failed with the
          // server's reason, for the user to read and discard by tapping
          // the sync pill. It used to be deleted here with only a
          // console.warn, so a death or a weighing could vanish from the
          // phone with nothing on screen to say so.
          console.warn("[kudde] outbox entry refused by the server", entry, e);
          await IDB.markFailed(entry.uuid, Kudde.errorDetail(e));
          continue;
        }
        Kudde.setOffline(true);
        break; // still offline - stop and retry the whole batch next time
      }
    }

    await refreshSyncStatusPill();
    await loadAnimals();
      if (activeTag && document.getElementById("animalDetailModal").classList.contains("flex")) {
      await renderAnimalPhotos();
    }
  } finally {
    syncBusy = false;
  }
}

// ---------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------

async function init() {
  document.getElementById("appVersion").textContent = `v${Kudde.VERSION}`;
  Kudde.offlineBanner("Offline - changes are saved on this device and will sync later");
  Kudde.onOfflineChange = () => { refreshSyncStatusPill().catch(() => {}); };

  document.getElementById("searchInput").addEventListener("input", renderAnimalList);
  initDateChips();
  renderToday();
  document.getElementById("todayToggle").addEventListener("click", () => {
    const list = document.getElementById("todayList");
    list.classList.toggle("hidden");
    document.getElementById("todayChevron").classList.toggle("fa-chevron-down", list.classList.contains("hidden"));
    document.getElementById("todayChevron").classList.toggle("fa-chevron-up", !list.classList.contains("hidden"));
  });
  const setListFilter = (value) => {
    listFilter = value;
    try { localStorage.setItem(LIST_FILTER_KEY, value); } catch (_) { /* not remembered, still applied */ }
    renderAnimalList();
  };
  document.getElementById("filterAliveBtn").addEventListener("click", () => setListFilter("alive"));
  document.getElementById("filterAllBtn").addEventListener("click", () => setListFilter("all"));
  document.getElementById("needsWeighingBtn").addEventListener("click",
    () => setListFilter(listFilter === "weigh" ? "alive" : "weigh"));
  document.getElementById("eventValue").addEventListener("input", () => {
    showWeightHint(document.getElementById("eventValueHint"), animals.find((a) => a.tag === activeTag),
                   document.getElementById("eventValue").value);
  });

  document.getElementById("weighSessionBtn").addEventListener("click", () => openSessionSetup("weight"));
  document.getElementById("treatSessionBtn").addEventListener("click", () => openSessionSetup("treatment"));
  document.getElementById("cancelSessionBtn").addEventListener("click", closeSession);
  document.getElementById("startSessionBtn").addEventListener("click", startSession);
  document.getElementById("doneSessionBtn").addEventListener("click", closeSession);
  document.getElementById("sessionSaveBtn").addEventListener("click", () => { saveSessionEntry().catch(() => {}); });
  document.getElementById("sessionTag").addEventListener("input", syncSessionEntry);
  document.getElementById("sessionValue").addEventListener("input", syncSessionEntry);
  // Enter walks the two boxes the way a keypad user expects: tag, then kg,
  // then save - a treat session has no kg box, so Enter on the tag saves.
  document.getElementById("sessionTag").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (session.kind === "weight") document.getElementById("sessionValue").focus();
    else saveSessionEntry().catch(() => {});
  });
  document.getElementById("sessionValue").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); saveSessionEntry().catch(() => {}); }
  });
  document.getElementById("syncStatus").addEventListener("click", () => { reviewFailedEntries().catch(() => {}); });
  document.getElementById("addAnimalBtn").addEventListener("click", openAddAnimal);
  document.getElementById("newFemaleBtn").addEventListener("click", () => setNewSex("female"));
  document.getElementById("newMaleBtn").addEventListener("click", () => setNewSex("male"));
  document.getElementById("cancelAddAnimalBtn").addEventListener("click", closeAddAnimal);
  document.getElementById("confirmAddAnimalBtn").addEventListener("click", confirmAddAnimal);
  document.getElementById("closeDetailBtn").addEventListener("click", closeAnimalDetail);
  document.getElementById("photoViewer").addEventListener("click", closePhotoViewer);
  document.getElementById("cancelEventBtn").addEventListener("click", closeEventModal);
  document.getElementById("confirmEventBtn").addEventListener("click", confirmEvent);
  document.getElementById("moveCampBtn").addEventListener("click", openMoveModal);
  document.getElementById("cancelMoveBtn").addEventListener("click", closeMoveModal);
  document.getElementById("backMoveBtn").addEventListener("click", backToMoveLocations);
  document.getElementById("confirmMoveBtn").addEventListener("click", confirmMove);

  document.querySelectorAll("#animalDetailModal .action-btn[data-kind]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.kind === "birth") openCalving();
      else openEventModal(btn.dataset.kind);
    });
  });
  document.getElementById("cancelCalvingBtn").addEventListener("click", closeCalving);
  document.getElementById("confirmCalvingBtn").addEventListener("click", () => { confirmCalving().catch(() => {}); });
  document.getElementById("calfFemaleBtn").addEventListener("click", () => setCalfSex("female"));
  document.getElementById("calfMaleBtn").addEventListener("click", () => setCalfSex("male"));

  document.getElementById("takePhotoBtn").addEventListener("click", () => {
    document.getElementById("photoInput").click();
  });
  document.getElementById("photoInput").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = ""; // so choosing the same file again still fires "change"
    if (file && activeTag) await uploadAnimalPhoto(activeTag, file);
  });

  KWPTR.attach(async () => { await loadAnimals(); await trySync(); });
  window.addEventListener("online", trySync);

  await loadAnimals();
  await trySync();
  setInterval(trySync, 20000);
}

init().catch((e) => {
  console.error("[kudde] failed to start", e);
  document.body.innerHTML = `<div class="p-6 text-center text-red-600">${escapeHtml(startupFailureMessage(e))}</div>`;
});

// The Tailscale-only 403 (security.py) is the one startup failure a user
// can fix themselves, and its message already says how - so it is shown on
// its own. "Failed to start: 403 Kudde is only reachable..." buried the
// instruction behind a status code and made a wrong address read as a
// crash. Anything else keeps the prefix, because then something IS broken.
function startupFailureMessage(e) {
  const raw = String((e && e.message) || e || "");
  if (/^403\b/.test(raw)) return Kudde.errorDetail(e);
  return `Failed to start: ${raw}`;
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(() => {});
}
