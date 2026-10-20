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

  async load() {
    KuddeProducts.list = await Kudde.cachedList(KuddeProducts.STORAGE_KEY, "/api/products");
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
      });
      container.appendChild(el);
    }
  },

  // The three treatment boxes as the server wants them, from the same
  // {product, dose, withdrawal} inputs render() fills. A blank withdrawal
  // is null (none recorded), not zero - zero means "may be sold today" -
  // and anything else that is not a whole number of days is refused here,
  // with the toast, so every form checks it the same way. Returns the
  // fields, or null after toasting.
  read(inputs) {
    const raw = inputs.withdrawal.value.trim();
    const days = /^\d+$/.test(raw) ? parseInt(raw, 10) : null;
    if (raw && days === null) { Kudde.toast("Withdrawal must be a whole number of days"); return null; }
    return { product: inputs.product.value.trim(), dose: inputs.dose.value.trim(), withdrawal_days: days };
  },
};
