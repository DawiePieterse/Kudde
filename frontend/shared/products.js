// The bottles used before, as chips on a treatment form. Same shape and
// same reasons as shared/camps.js: one list for Field and Admin, fetched
// from GET /api/products and kept in localStorage so it is there with no
// signal. Nothing to set up - the list is the treatments already on file,
// most recently used first, each with the dose and withdrawal it was last
// given with. A chip fills all three boxes; the boxes stay editable, so a
// different dose this time is typed over, not fought with.
const KuddeProducts = {
  STORAGE_KEY: "kudde_products",
  list: [],

  _readCache() {
    try {
      const parsed = JSON.parse(localStorage.getItem(KuddeProducts.STORAGE_KEY) || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch (_) { return []; }
  },

  async load() {
    try {
      KuddeProducts.list = await Kudde.api("/api/products");
      try { localStorage.setItem(KuddeProducts.STORAGE_KEY, JSON.stringify(KuddeProducts.list)); } catch (_) { /* full or private */ }
      Kudde.setOffline(false);
    } catch (e) {
      if (!Kudde.isNetworkError(e)) throw e;
      Kudde.setOffline(true);
      KuddeProducts.list = KuddeProducts._readCache();
    }
    return KuddeProducts.list;
  },

  // A treatment just recorded goes to the front of the list at once, so
  // the next animal in the same session sees it without a round trip.
  remember(entry) {
    if (!entry.product) return;
    KuddeProducts.list = [entry, ...KuddeProducts.list.filter(
      (p) => p.product.toLowerCase() !== entry.product.toLowerCase())].slice(0, 20);
    try { localStorage.setItem(KuddeProducts.STORAGE_KEY, JSON.stringify(KuddeProducts.list)); } catch (_) { /* as above */ }
  },

  // Draws one chip per product into `container`. Picking one fills the
  // product, dose and withdrawal inputs. Nothing on file: no chips, just
  // the boxes.
  render(container, inputs) {
    container.innerHTML = "";
    const current = inputs.product.value.trim().toLowerCase();
    for (const p of KuddeProducts.list) {
      const el = document.createElement("button");
      el.type = "button";
      el.className = "chip" + (p.product.toLowerCase() === current ? " active" : "");
      el.textContent = p.product;
      el.addEventListener("click", () => {
        inputs.product.value = p.product;
        inputs.dose.value = p.dose || "";
        inputs.withdrawal.value = p.withdrawal_days == null ? "" : p.withdrawal_days;
        container.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c === el));
        if (typeof inputs.onPick === "function") inputs.onPick();
      });
      container.appendChild(el);
    }
  },
};
