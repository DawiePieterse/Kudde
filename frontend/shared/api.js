// Shared helpers used by the field and admin apps: a fetch wrapper with a
// timeout, offline detection/banner, and date formatting. Much smaller than
// Boord's equivalent (frontend/shared/api.js) - Kudde has no accounts, no
// devices, no picking slips, just animals and events on one local server.
const API_BASE = "";

const Kudde = {
  // Bump on every deploy that touches frontend code, shown in each screen's
  // header - useful given the service workers cache the shell first.
  VERSION: "0.1",

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

  fmtDate(value, fallback = "") {
    if (!value) return fallback;
    const d = new Date(value.length <= 10 ? `${value}T00:00:00` : value);
    return isNaN(d.getTime()) ? fallback : d.toLocaleDateString();
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
    window.addEventListener("offline", () => Kudde.setOffline(true));
    window.addEventListener("online", () => Kudde.setOffline(false));
    if (!navigator.onLine) Kudde._offline = true;
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

  toast(message) {
    let el = document.getElementById("kudde-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "kudde-toast";
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(Kudde._toastTimer);
    Kudde._toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
  },
};
