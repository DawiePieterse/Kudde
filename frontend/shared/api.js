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

  eventLabel(kind) {
    const key = String(kind || "");
    return Kudde.EVENT_LABEL[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : "");
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
