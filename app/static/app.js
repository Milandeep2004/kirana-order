(() => {
"use strict";
const $app = document.getElementById("app");
const state = { me: null, cat: null, cart: {}, lang: "en", filter: "all", offline: false, last: null };

// ---------- helpers ----------
const t = (k) => (I18N[state.lang] && I18N[state.lang][k]) || I18N.en[k] || k;
const rupees = (p) => "₹" + (p / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage may be blocked */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};
// Build DOM with textContent only: nothing from the server is ever parsed as HTML.
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else if (k === "style") el.style.cssText = v; // CSSOM is allowed by our CSP; a style attribute is not
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
const svg = (tag, attrs) => { const e = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined });
  } catch { const e = new Error("offline"); e.offline = true; throw e; }
  if (res.status === 401 && path !== "/api/auth/login") { state.me = null; go("#/login"); throw new Error("auth"); }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) { const e = new Error(typeof data?.detail === "string" ? data.detail : data?.detail?.message || "Something went wrong."); e.status = res.status; e.data = data; throw e; }
  return data;
}

// ---------- cart (kept on the device) ----------
const loadCart = () => { try { state.cart = JSON.parse(store.get("cart") || "{}"); } catch { state.cart = {}; } };
const saveCart = () => store.set("cart", JSON.stringify(state.cart));
const cartCount = () => Object.values(state.cart).reduce((a, b) => a + b, 0);
function setQty(code, q) { q = Math.max(0, Math.min(10000, q | 0)); if (q) state.cart[code] = q; else delete state.cart[code]; saveCart(); }

// ---------- shell ----------
function header() {
  return h("header", { class: "top" }, h("div", { class: "wrap" },
    h("div", { class: "brand" }, t("appName"), state.me?.shop && h("span", { class: "shop", text: state.me.shop })),
    h("button", { class: "link", type: "button", onclick: toggleLang }, t("language")),
    state.me && h("button", { class: "link", type: "button", onclick: logout }, t("logout"))));
}
function render(...parts) { $app.replaceChildren(header(), h("main", { class: "wrap" }, ...parts.flat())); }
function toggleLang() { state.lang = state.lang === "en" ? "hi" : "en"; store.set("lang", state.lang); document.documentElement.lang = state.lang; route(); }
async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch { /* ignore */ }
  navigator.serviceWorker?.controller?.postMessage("clear-api");
  state.me = null; state.cat = null; state.cart = {}; store.del("cart"); go("#/login");
}
const go = (hash) => { if (location.hash === hash) route(); else location.hash = hash; };
const skeleton = () => h("div", { class: "grid", "aria-busy": "true" }, [1, 2, 3, 4].map(() => h("div", { class: "skel" })));
const errorBox = (msg, retry) => h("div", { class: "empty" }, h("p", { class: "msg err", text: msg }), h("button", { class: "btn sm", type: "button", onclick: retry }, t("retry")));

// ---------- login ----------
async function viewLogin() {
  let cfg = { demo_mode: false };
  try { cfg = await api("/api/config"); } catch { /* login still works without it */ }
  let phone = "", step = "phone", error = "";
  const draw = () => {
    const form = h("form", { novalidate: true, onsubmit: async (e) => {
      e.preventDefault(); error = "";
      const val = new FormData(form).get(step === "phone" ? "phone" : "otp").trim();
      try {
        if (step === "phone") {
          if (!/^[6-9]\d{9}$/.test(val)) throw new Error(t("enterPhone"));
          phone = val; await api("/api/auth/request-otp", { method: "POST", body: { phone } }); step = "otp";
        } else {
          state.me = null; await api("/api/auth/login", { method: "POST", body: { phone, otp: val } });
          await boot(); return;
        }
      } catch (err) { error = err.status === 401 ? t("wrongCode") : err.offline ? t("offlineCart") : err.message; }
      draw();
    } },
      step === "phone"
        ? h("label", { class: "field" }, h("span", { text: t("phone") }), h("input", { name: "phone", inputmode: "numeric", autocomplete: "tel", maxlength: 10, value: phone, required: true, autofocus: true }))
        : h("label", { class: "field" }, h("span", { text: `${t("code")} (${phone})` }), h("input", { name: "otp", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 8, required: true, autofocus: true })),
      error && h("p", { class: "msg err", role: "alert", text: error }),
      h("button", { class: "btn", type: "submit" }, step === "phone" ? t("sendCode") : t("verify")));
    const demo = cfg.demo_mode && h("div", { class: "demo" }, h("p", { text: `${t("demoHint")} ${t("demoCode")}: ${cfg.demo_otp}` }),
      cfg.demo_users.map((u) => h("button", { class: "btn secondary", type: "button", onclick: async () => {
        try { await api("/api/auth/login", { method: "POST", body: { phone: u.phone, otp: cfg.demo_otp } }); await boot(); }
        catch (err) { error = err.message; draw(); }
      } }, u.label)));
    $app.replaceChildren(header(), h("main", { class: "wrap login" }, h("h1", { text: t("login") }), h("p", { class: "sub", text: t("appName") }), form, demo));
  };
  draw();
}

// ---------- catalogue ----------
function packIcon(category) {
  const s = svg("svg", { viewBox: "0 0 64 80", "aria-hidden": "true" });
  const body = category === "Tea" ? "M14 22h36l-4 50H18z" : category === "Biscuits" ? "M8 26h48v38H8z" : "M16 14h32v58H16z";
  s.append(svg("path", { d: body, fill: "#fff", "fill-opacity": ".85", stroke: "#1c1c1a", "stroke-opacity": ".25", "stroke-width": "2" }));
  s.append(svg("rect", { x: "20", y: "36", width: "24", height: "14", rx: "3", fill: "#1f6f4a", "fill-opacity": ".85" }));
  return s;
}
const stockLabel = (s) => (s === "out" ? t("outStock") : s === "low" ? t("lowStock") : t("inStock"));

function stepper(code, onChange) {
  const out = h("output", { text: String(state.cart[code] || 0) });
  const bump = (d) => { setQty(code, (state.cart[code] || 0) + d); out.textContent = String(state.cart[code] || 0); onChange(); };
  return h("div", { class: "step" },
    h("button", { type: "button", "aria-label": "−", onclick: () => bump(-1) }, "−"), out,
    h("button", { type: "button", "aria-label": "+", onclick: () => bump(1) }, "+"));
}

async function viewCatalogue() {
  render(h("div", { class: "grid" }, skeleton()));
  const draw = () => {
    const items = state.cat.items;
    const cats = ["all", ...new Set(items.map((i) => i.category))];
    const bar = h("div", { class: "bar", hidden: true });
    const updateBar = () => {
      const n = cartCount(); bar.hidden = n === 0;
      bar.replaceChildren(h("div", { class: "inner" }, h("div", { class: "info", text: `${n} ${t("cases")}` }),
        h("button", { class: "btn", type: "button", onclick: () => go("#/cart") }, t("viewCart"))));
    };
    render(
      state.offline && h("p", { class: "msg warn", text: t("offline") }),
      h("div", { class: "chips", role: "group" }, cats.map((c) => h("button", { class: "chip", type: "button", "aria-pressed": String(state.filter === c),
        onclick: () => { state.filter = c; draw(); } }, c === "all" ? t("all") : c))),
      h("p", { class: "gst", text: t("exclGst") }),
      h("div", { class: "grid" }, items.filter((i) => state.filter === "all" || i.category === state.filter).map((i) =>
        h("article", { class: "card" },
          h("div", { class: "pic", style: `background:${i.color}` }, packIcon(i.category), i.schemes[0] && h("span", { class: "badge", text: t("offer") })),
          h("div", { class: "body" }, h("h3", { text: i.name }),
            h("div", { class: "price" }, rupees(i.case_price_paise), " ", h("small", { text: t("perCase") })),
            h("div", { class: "sub2", text: `${rupees(i.piece_price_paise)} ${t("perPiece")} · ${t("exGst")}` }),
            h("div", { class: "schemes" }, i.schemes.map((s) => h("span", { class: "scheme", text: s }))),
            h("div", { class: `stock ${i.stock_status}`, text: stockLabel(i.stock_status) }),
            i.stock_status === "out" ? h("button", { class: "btn secondary", type: "button", disabled: true }, t("outStock")) : stepper(i.code, updateBar))))),
      bar);
    updateBar();
  };
  try {
    state.cat = await api("/api/catalogue"); state.offline = false;
  } catch (e) {
    if (e.message === "auth") return;
    if (e.offline) { try { const r = await caches.match("/api/catalogue"); if (r) { state.cat = await r.json(); state.offline = true; } } catch { /* no cache */ } }
    if (!state.cat) return render(errorBox(e.offline ? t("offlineCart") : e.message, viewCatalogue));
  }
  draw();
}

// ---------- cart ----------
async function viewCart() {
  const codes = Object.keys(state.cart);
  if (!codes.length) return render(h("div", { class: "empty" }, h("h2", { text: t("emptyCart") }), h("button", { class: "btn sm", type: "button", onclick: () => go("#/catalogue") }, t("browse"))));
  render(skeleton());
  let priced;
  try { priced = await api("/api/cart/price", { method: "POST", body: { lines: codes.map((c) => ({ sku_code: c, cases: state.cart[c] })) } }); }
  catch (e) { if (e.message === "auth") return; return render(errorBox(e.offline ? t("offlineCart") : e.message, viewCart)); }
  const me = state.me, credit = me.credit_limit_paise - me.outstanding_paise;
  const over = priced.total_paise > credit;
  const saved = priced.lines.reduce((a, l) => a + l.discount_paise, 0);
  const refresh = () => viewCart();
  let busy = false, placeErr = "";
  const place = async (btn) => {
    if (busy) return; busy = true; btn.disabled = true;
    try {
      const o = await api("/api/orders", { method: "POST", body: { lines: codes.map((c) => ({ sku_code: c, cases: state.cart[c] })) } });
      state.cart = {}; store.del("cart"); state.last = o; go("#/done");
    } catch (e) { busy = false; btn.disabled = false; placeErr = e.message; refresh(); }
  };
  const placeBtn = h("button", { class: "btn", type: "button", disabled: !priced.placeable, onclick: (e) => place(e.currentTarget) }, t("placeOrder"));
  render(
    priced.lines.map((l) => h("div", { class: "line" },
      h("div", { class: "row" }, h("span", { class: "name", text: l.name }), h("button", { class: "link", type: "button", onclick: () => { setQty(l.sku_code, 0); refresh(); } }, t("remove"))),
      h("div", { class: "row" }, stepper(l.sku_code, refresh), h("strong", { text: rupees(l.net_paise) })),
      l.free_cases > 0 && h("div", { class: "note", text: `+ ${l.free_cases} ${t("cases")} ${t("free")}` }),
      l.notes.filter((n) => !/free/.test(n)).map((n) => h("div", { class: "note", text: n })),
      l.issues.map((i) => h("div", { class: "msg err", role: "alert" }, i.message, " ",
        i.max_orderable > 0 && h("button", { class: "link", type: "button", onclick: () => { setQty(l.sku_code, i.max_orderable); refresh(); } }, `${t("reduceTo")} ${i.max_orderable}`))))),
    h("div", { class: "totals" },
      saved > 0 && h("div", { class: "row" }, h("span", { text: t("youSave") }), h("span", { text: "− " + rupees(saved) })),
      h("div", { class: "row grand" }, h("span", { text: t("total") }), h("span", { text: rupees(priced.total_paise) })),
      h("div", { class: "sub2", text: t("exclGst") }),
      h("div", { class: "sub2", text: `${t("creditLeft")}: ${rupees(Math.max(credit, 0))}` })),
    over && h("p", { class: "msg warn", text: t("holdWarn") }),
    placeErr && h("p", { class: "msg err", role: "alert", text: placeErr }),
    placeBtn);
}

function viewDone() {
  const o = state.last;
  if (!o) return go("#/catalogue");
  render(h("div", { class: "done" }, h("div", { class: "tick", "aria-hidden": "true" }, "✓"),
    h("h2", { text: o.status === "on_hold" ? t("orderHeld") : t("orderPlaced") }),
    h("p", { text: `${t("orderRef")}: #${o.id} · ${rupees(o.total_paise)}` }),
    o.status === "on_hold" && h("p", { class: "msg warn", text: o.credit_hold_reason }),
    h("button", { class: "btn", type: "button", onclick: () => go("#/catalogue") }, t("keepShopping"))));
}

// ---------- routing ----------
function route() {
  const r = location.hash || "#/catalogue";
  if (!state.me) return r === "#/login" ? viewLogin() : go("#/login");
  if (state.me.role === "desk") return render(h("p", { class: "msg info", text: t("deskSoon") }));
  if (r === "#/cart") return viewCart();
  if (r === "#/done") return viewDone();
  if (r === "#/login") return go("#/catalogue");
  return viewCatalogue();
}
async function boot() {
  try { state.me = await api("/api/me"); } catch { state.me = null; }
  if (state.me && (!location.hash || location.hash === "#/login")) location.hash = "#/catalogue";
  route();
}
state.lang = store.get("lang") === "hi" ? "hi" : "en"; document.documentElement.lang = state.lang;
loadCart();
window.addEventListener("hashchange", route);
if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
boot();
})();
