// Shared helpers used by the field and admin apps: a fetch wrapper with a
// timeout, offline detection/banner, and date formatting. Much smaller than
// Boord's equivalent (frontend/shared/api.js) - Kudde has no accounts, no
// devices, no picking slips, just animals and events on one local server.
const API_BASE = "";

const Kudde = {
  // Bump on every deploy that touches frontend code, shown in each screen's
  // header - useful given the service workers cache the shell first.
  VERSION: "0.7",

  // A device whose WiFi is up but that cannot actually reach the farm server
  // gets no error from fetch() - the request just hangs until the OS gives
  // up. Every request therefore carries a deadline, and a blown deadline is
  // reported as a normal network failure so callers fall back to the
  // offline queue instead of waiting.
  NETWORK_TIMEOUT_MS: 8000,

  async _fetchWithTimeout(url, options = {}, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs || Kudde.NETWORK_TIMEOUT_MS);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  },

  // True when a request failed because the server could not be reached
  // (offline, unreachable, timed out) rather than because it answered with
  // an error - callers use this to decide whether to queue for later.
  isNetworkError(e) {
    return e instanceof TypeError || (!!e && (e.name === "AbortError" || e.name === "TimeoutError"));
  },

  // The human-readable half of a server rejection. api() throws
  // `${status} ${body}` and FastAPI puts its message in a JSON "detail"
  // field, so showing e.message raw would hand the user a status code and a
  // lump of JSON.
  errorDetail(e, fallback = "Something went wrong") {
    const raw = e && typeof e.message === "string" ? e.message : String(e || "");
    const body = raw.replace(/^\d{3}\s*/, "");
    try {
      const parsed = JSON.parse(body);
      if (parsed && typeof parsed.detail === "string") return parsed.detail;
      // A 422 from FastAPI's own validation carries detail as a LIST of
      // {loc, msg, type} objects, one per bad field. Showing that raw would
      // put a lump of JSON in a toast; the first message is what the user
      // can act on ("Input should be greater than 0"), and a form only ever
      // has one thing wrong with it at a time in practice.
      if (parsed && Array.isArray(parsed.detail) && parsed.detail.length
          && parsed.detail[0] && typeof parsed.detail[0].msg === "string") {
        return parsed.detail[0].msg;
      }
    } catch (_) { /* not JSON - fall through to the raw text */ }
    return body.trim() || fallback;
  },

  async api(path, { method = "GET", body, timeoutMs } = {}) {
    const headers = {};
    let payload = body;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const res = await Kudde._fetchWithTimeout(
      `${API_BASE}${path}`, { method, headers, body: payload }, timeoutMs);
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`${res.status} ${text}`);
    }
    const contentType = res.headers.get("content-type") || "";
    return contentType.includes("application/json") ? res.json() : res.text();
  },

  // Like api(), but for multipart uploads (photos): the body is a FormData,
  // so the browser must set its own Content-Type (with the boundary) -
  // setting one manually here would break the upload.
  async apiUpload(path, formData, { timeoutMs } = {}) {
    const res = await Kudde._fetchWithTimeout(
      `${API_BASE}${path}`, { method: "POST", body: formData }, timeoutMs);
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`${res.status} ${text}`);
    }
    return res.json();
  },

  // Display formatting is pinned to en-ZA rather than left to the device's
  // own locale. The phones on this farm are a mix of whatever they shipped
  // with - a US-set iPhone prints 3/4/2026 and a 12-hour clock, which on a
  // treatment record reads as the wrong month - and the siblings (Boord,
  // Owner, Labour) already show South African dates, so the same weighing
  // should not read differently depending on which screen it is seen on.
  // These are for SHOWING values only: anything sent to the server (or put
  // in an <input type=date>) stays YYYY-MM-DD via localDateStr below.
  LOCALE: "en-ZA",

  // A bare "YYYY-MM-DD" is a calendar date and is read as local midnight;
  // a date-time with no zone marker is the server's naive UTC (models.py
  // stamps created_at with datetime.utcnow) and is pinned to UTC first, or
  // a SAST phone would print it two hours slow.
  _parseDate(value) {
    if (value === null || value === undefined || value === "") return null;
    if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
    let s = String(value).trim();
    if (s.length <= 10) {
      s = `${s}T00:00:00`;
    } else if (/\d{1,2}:\d{2}/.test(s)) {
      s = s.replace(" ", "T");
      if (!/(Z|[+-]\d{2}:?\d{2})$/.test(s)) s += "Z";
    }
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  },

  fmtDate(value, fallback = "") {
    const d = Kudde._parseDate(value);
    return d ? d.toLocaleDateString(Kudde.LOCALE) : fallback;
  },

  // 24-hour on purpose (hour12: false): "02:00" can't be misread as the
  // afternoon the way "2:00" without an am/pm can.
  fmtTime(value, fallback = "") {
    const d = Kudde._parseDate(value);
    return d ? d.toLocaleTimeString(Kudde.LOCALE, { hour: "2-digit", minute: "2-digit", hour12: false }) : fallback;
  },

  fmtDateTime(value, fallback = "") {
    const d = Kudde._parseDate(value);
    return d ? `${Kudde.fmtDate(d)} ${Kudde.fmtTime(d)}` : fallback;
  },

  // "312.5 kg" - one decimal at most, so a scale's 312.50000001 doesn't
  // reach the screen, and the space before the unit the way it is written
  // on the weighbridge slips. Null/blank gives the fallback, not "NaN kg".
  fmtKg(n, fallback = "") {
    if (n === null || n === undefined || n === "") return fallback;
    const num = Number(n);
    if (!isFinite(num)) return fallback;
    return `${num.toLocaleString(Kudde.LOCALE, { maximumFractionDigits: 1 })} kg`;
  },

  // What each event kind is called on screen. The server's keys are
  // lowercase identifiers ("weight", "death"); printing them raw in a
  // history list reads as a debug dump. Unknown kinds fall back to the key
  // with a capital, so a kind added on the server still reads sensibly
  // before this map learns about it.
  EVENT_LABEL: {
    birth: "Birth", weight: "Weight", treatment: "Treatment",
    movement: "Movement", death: "Death", sale: "Sale",
  },

  // "Ivermectin 5 ml, 28 days withdrawal" - what a treatment event was,
  // for a history line. Empty for a treatment that is only a note.
  treatmentSummary(e) {
    if (!e || e.kind !== "treatment") return "";
    const parts = [];
    if (e.product) parts.push(e.dose ? `${e.product} ${e.dose}` : e.product);
    if (e.withdrawal_days != null) parts.push(`${e.withdrawal_days} day${e.withdrawal_days === 1 ? "" : "s"} withdrawal`);
    return parts.join(", ");
  },

  // True while `until` (a YYYY-MM-DD) is today or later: the animal may
  // not be sold or slaughtered yet. Compared as calendar dates in local
  // time, so the day the withdrawal ends still counts.
  inWithdrawal(until) {
    if (!until) return false;
    return String(until) >= Kudde.localDateStr();
  },

  // Whole days since a YYYY-MM-DD, or null when there is no date.
  daysSince(dateStr) {
    const d = Kudde._parseDate(dateStr);
    if (!d) return null;
    return Math.floor((Date.now() - d.getTime()) / 86400000);
  },

  // What to show under a kg box for `animal`, given what has been typed so
  // far: the last weighing, the change since, and a nudge when the number
  // is a long way off. One rule for both apps. Returns {text, warn}.
  weightHint(animal, typed) {
    if (!animal) return { text: "", warn: false };
    if (animal.last_weight == null) return { text: "First weighing", warn: false };
    const days = Kudde.daysSince(animal.last_weight_date);
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
  },

  showWeightHint(el, animal, typed) {
    const { text, warn } = Kudde.weightHint(animal, typed);
    el.textContent = text;
    el.classList.toggle("hint-warn", warn);
  },

  // What the sheet before a sale has to say if the animal is still in
  // withdrawal: the one thing that can cost the farm its buyer. A warning,
  // not a refusal - the sale may be to another farm. Empty when clear.
  saleWarning(animal) {
    return animal && Kudde.inWithdrawal(animal.withdrawal_until)
      ? `${animal.tag} is in withdrawal until ${Kudde.fmtDate(animal.withdrawal_until)} and should not be slaughtered before then.`
      : "";
  },

  // A list fetched from `path` and kept in localStorage under `key`, so it
  // is there with no signal: the camps and the products both work this
  // way. Never throws for a network failure - the caller is loading a
  // screen and a missing list must not stop it.
  async cachedList(key, path) {
    try {
      const fresh = await Kudde.api(path);
      try { localStorage.setItem(key, JSON.stringify(fresh)); } catch (_) { /* full or private */ }
      Kudde.setOffline(false);
      return fresh;
    } catch (e) {
      if (!Kudde.isNetworkError(e)) throw e;
      Kudde.setOffline(true);
      try {
        const parsed = JSON.parse(localStorage.getItem(key) || "[]");
        return Array.isArray(parsed) ? parsed : [];
      } catch (_) { return []; }
    }
  },

  // "Heifer calf" from "heifer calf": the server's class names are lower
  // case so they read inside a sentence; a row or a column wants a capital.
  classLabel(cls) {
    const s = String(cls || "");
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
  },

  // The class an animal would have, worked out on the device, for one the
  // server has not answered about yet (added offline). Same rules as
  // backend/classes.py, less "has calved", which the phone cannot know for
  // a new animal - and a new animal has not.
  localClass(a) {
    const CALF_UNTIL_DAYS = 365;
    const born = Kudde._parseDate(a.birth_date);
    const isCalf = born && (Date.now() - born.getTime()) / 86400000 < CALF_UNTIL_DAYS;
    if (a.sex === "female") return isCalf ? "heifer calf" : "heifer";
    const base = a.castrated ? "steer" : "bull";
    return isCalf ? `${base} calf` : base;
  },

  eventLabel(kind) {
    const key = String(kind || "");
    return Kudde.EVENT_LABEL[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : "");
  },

  // Tag order as a farmer reads it: B2 before B10, A20 before A100. Plain
  // string order sorts digit by digit, so B10 landed between B1 and B2 and
  // a herd numbered past nine never read in order. This is a literal port
  // of backend/tags.py tag_sort_key (digit runs as numbers, letter runs
  // ignoring case, a number before text at the same position) rather than
  // localeCompare's numeric mode, because ICU and Python disagree on
  // spaces, hyphens and accents, and a row that sorts differently on the
  // phone and the server moves between the list and the Move picker.
  tagKey(tag) {
    return String(tag || "").split(/(\d+)/).filter(Boolean)
      .map((part) => (/^\d+$/.test(part) ? [0, parseInt(part, 10)] : [1, part.toLowerCase()]));
  },

  compareTags(a, b) {
    const ka = Kudde.tagKey(a);
    const kb = Kudde.tagKey(b);
    for (let i = 0; i < Math.min(ka.length, kb.length); i++) {
      if (ka[i][0] !== kb[i][0]) return ka[i][0] - kb[i][0];
      if (ka[i][1] !== kb[i][1]) return ka[i][1] < kb[i][1] ? -1 : 1;
    }
    return ka.length - kb.length;
  },

  // "Today" as a YYYY-MM-DD string in local time - what a <input type=date>
  // wants. toISOString() would give the UTC date, wrong before dawn.
  localDateStr(d = new Date()) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  },

  // Slim amber banner pinned under the header telling the user the screen is
  // offline. Screens should ALSO call Kudde.setOffline(true/false) from
  // their own request results: navigator.onLine only reflects the radio,
  // not whether the local server is actually reachable.
  offlineBanner(message) {
    let el = document.getElementById("kudde-offline-banner");
    if (!el) {
      el = document.createElement("div");
      el.id = "kudde-offline-banner";
      el.className = "offline-banner hidden";
      const header = document.querySelector(".kudde-header");
      if (header && header.parentNode) header.parentNode.insertBefore(el, header.nextSibling);
      else document.body.prepend(el);
    }
    el.innerHTML = `<i class="fa-solid fa-wifi"></i> ${message}`;

    // Registered once, however many times this is called. They used to be
    // added on every call, so a screen that re-ran its setup accumulated a
    // fresh pair of listeners each time and fired onOfflineChange once per
    // registration on the next flip.
    if (!Kudde._offlineListenersBound) {
      Kudde._offlineListenersBound = true;
      window.addEventListener("offline", () => Kudde.setOffline(true));
      window.addEventListener("online", () => Kudde.setOffline(false));
    }

    // Through setOffline rather than by assigning _offline directly, so a
    // screen that starts up with the radio already off gets the same
    // onOfflineChange callback it would get from a flip. Assigning the flag
    // here left setOffline(true) with nothing to do when the first request
    // then failed, and the screen's own status pill was never told.
    Kudde.setOffline(!navigator.onLine);
    el.classList.toggle("hidden", !Kudde._offline);
  },

  setOffline(isOffline) {
    const val = !!isOffline;
    if (Kudde._offline === val) return; // only react to actual flips
    Kudde._offline = val;
    const el = document.getElementById("kudde-offline-banner");
    if (el) el.classList.toggle("hidden", !val);
    if (typeof Kudde.onOfflineChange === "function") Kudde.onOfflineChange(val);
  },

  isOffline() { return !!Kudde._offline; },
  onOfflineChange: null,

  // A confirmation sheet in place of window.confirm(). Native confirm()
  // reads as a browser error box on an iPhone, says "OK" for an action that
  // takes an animal out of the herd, and cannot colour the dangerous
  // choice. This one names the action on its button, paints it red when
  // `danger` is set, and lets a WARNING line stand out instead of being
  // the third paragraph of a grey dialog. Resolves true for the action,
  // false for the way out (button, backdrop tap or Escape).
  confirm({ title, message = "", warning = "", confirmLabel = "OK", cancelLabel = "Cancel", danger = false } = {}) {
    return new Promise((resolve) => {
      const backdrop = document.createElement("div");
      backdrop.className = "sheet-backdrop";
      backdrop.setAttribute("role", "dialog");
      backdrop.setAttribute("aria-modal", "true");
      const lines = String(message).split("\n").filter((l) => l.trim());
      backdrop.innerHTML = `
        <div class="sheet">
          <div class="sheet-title"></div>
          <div class="sheet-body">${lines.map(() => "<p></p>").join("")}</div>
          <div class="sheet-warning hidden"><i class="fa-solid fa-triangle-exclamation"></i> <span></span></div>
          <div class="sheet-actions">
            <button type="button" class="sheet-cancel"></button>
            <button type="button" class="sheet-confirm ${danger ? "sheet-danger" : ""}"></button>
          </div>
        </div>`;
      backdrop.querySelector(".sheet-title").textContent = title || "";
      backdrop.querySelectorAll(".sheet-body p").forEach((p, i) => { p.textContent = lines[i]; });
      if (warning) {
        backdrop.querySelector(".sheet-warning").classList.remove("hidden");
        backdrop.querySelector(".sheet-warning span").textContent = warning;
      }
      backdrop.querySelector(".sheet-cancel").textContent = cancelLabel;
      backdrop.querySelector(".sheet-confirm").textContent = confirmLabel;

      const finish = (answer) => {
        document.removeEventListener("keydown", onKey);
        backdrop.remove();
        resolve(answer);
      };
      const onKey = (e) => { if (e.key === "Escape") finish(false); };
      backdrop.querySelector(".sheet-cancel").addEventListener("click", () => finish(false));
      backdrop.querySelector(".sheet-confirm").addEventListener("click", () => finish(true));
      backdrop.addEventListener("click", (e) => { if (e.target === backdrop) finish(false); });
      document.addEventListener("keydown", onKey);
      document.body.appendChild(backdrop);
      backdrop.querySelector(".sheet-cancel").focus();
    });
  },

  // role=status + aria-live=polite so VoiceOver reads the message out
  // without stealing focus from whatever the user was doing.
  //
  // The time on screen grows with the message. A flat 2.2s was fine for
  // "Saved" but gone before anyone could finish reading a server rejection
  // or a list of failed sync reasons - so 2.2s plus 40ms a character, capped
  // at 6s so a long one doesn't sit over the screen indefinitely.
  toast(message) {
    let el = document.getElementById("kudde-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "kudde-toast";
      el.className = "toast";
      el.setAttribute("role", "status");
      el.setAttribute("aria-live", "polite");
      document.body.appendChild(el);
    }
    const text = String(message ?? "");
    el.textContent = text;
    el.classList.add("show");
    clearTimeout(Kudde._toastTimer);
    const duration = Math.min(6000, 2200 + 40 * text.length);
    Kudde._toastTimer = setTimeout(() => el.classList.remove("show"), duration);
  },
};
