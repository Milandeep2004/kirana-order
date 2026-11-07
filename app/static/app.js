(() => {
"use strict";
const $app = document.getElementById("app");
const state = { me: null, cat: null, cart: {}, lang: "en", filter: "all", offline: false, last: null };

// ---------- helpers ----------
const t = (k) => (I18N[state.lang] && I18N[state.lang][k]) || I18N.en[k] || k;
const fmt = (tpl, vars) => tpl.replace(/\{(\w+)\}/g, (_, k) => vars[k]);
const itemOf = (code) => state.cat?.items.find((x) => x.code === code);
const itemName = (code, fallback) => { const i = itemOf(code); return state.lang === "hi" && i?.name_hi ? i.name_hi : i?.name || fallback || code; };
const catLabel = (c) => t("cat_" + c) === "cat_" + c ? c : t("cat_" + c);
const ruleText = (r) => r.type === "free_goods" ? fmt(t("schemeFree"), { buy: r.buy_qty, free: r.free_qty }) : fmt(t("schemeSlab"), { pct: r.discount_pct, min: r.min_cases });
const ensureCat = async () => { if (!state.cat) { try { state.cat = await api("/api/catalogue"); } catch (e) { /* offline or logged out: callers fall back to server names */ } } };
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
function setQty(code, q) { q = Math.max(0, Math.min(10000, q | 0)); if (q) state.cart[code] = q; else delete state.cart[code]; saveCart(); updateCartTab(); }
function updateCartTab() { const el = document.getElementById("tab-cart"); if (el) el.textContent = cartCount() ? `${t("cart")} (${cartCount()})` : t("cart"); }

// ---------- shell ----------
function nav() {
  const cur = location.hash || "#/catalogue";
  const tabs = [["#/catalogue", t("catalogue")], ["#/message", t("byMessage")], ["#/orders", t("orders")], ["#/cart", cartCount() ? `${t("cart")} (${cartCount()})` : t("cart")]];
  return h("nav", { class: "tabs", "aria-label": "Main" }, tabs.map(([href, label]) => h("a", { href, class: "tab", id: href === "#/cart" ? "tab-cart" : false, "aria-current": cur === href ? "page" : false }, label)));
}
function header() {
  return h("header", { class: state.me?.role === "desk" ? "top wide" : "top" }, h("div", { class: "wrap" },
    h("div", { class: "brand" }, t("appName"), state.me?.shop && h("span", { class: "shop", text: state.me.shop })),
    state.me?.role !== "desk" && h("button", { class: "link", type: "button", onclick: toggleLang }, t("language")),
    state.me && h("button", { class: "link", type: "button", onclick: logout }, t("logout"))),
    state.me?.role === "retailer" && nav());
}
function render(...parts) { $app.replaceChildren(header(), h("main", { class: state.me?.role === "desk" ? "wrap wide" : "wrap" }, ...parts.flat())); }
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
        ? h("label", { class: "field" }, h("span", { text: t("phone") }), h("input", { name: "phone", inputmode: "numeric", autocomplete: "tel", maxlength: 10, value: phone, required: true, autofocus: !cfg.demo_mode }))
        : h("label", { class: "field" }, h("span", { text: `${t("code")} (${phone})` }), h("input", { name: "otp", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 8, required: true, autofocus: true, value: cfg.demo_mode ? cfg.demo_otp : "" })),
      error && h("p", { class: "msg err", role: "alert", text: error }),
      h("button", { class: "btn", type: "submit" }, step === "phone" ? t("sendCode") : t("verify")));
    const demo = cfg.demo_mode && h("section", { class: "demo" }, h("h2", { text: t("demoTitle") }), h("p", { class: "hint", text: t("demoHint") }),
      cfg.demo_users.map((u) => h("button", { class: "acct", type: "button", onclick: async () => {
        try { await api("/api/auth/login", { method: "POST", body: { phone: u.phone, otp: cfg.demo_otp } }); await boot(); }
        catch (err) { error = err.message; draw(); }
      } }, h("strong", { text: u.label }), h("span", { text: u.note }))));
    const own = h("div", { class: "own" }, cfg.demo_mode && h("p", { class: "or", text: t("orOwn") }), form);
    $app.replaceChildren(header(), h("main", { class: "wrap login" }, h("h1", { text: t("login") }), h("p", { class: "sub", text: t("appName") }), demo, own));
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
        onclick: () => { state.filter = c; draw(); } }, c === "all" ? t("all") : catLabel(c)))),
      h("p", { class: "gst", text: t("exclGst") }),
      h("div", { class: "grid" }, items.filter((i) => state.filter === "all" || i.category === state.filter).map((i) =>
        h("article", { class: "card" },
          h("div", { class: "pic", style: `background:${i.color}` }, packIcon(i.category), i.schemes[0] && h("span", { class: "badge", text: t("offer") })),
          h("div", { class: "body" }, h("h3", { text: itemName(i.code) }),
            h("div", { class: "price" }, rupees(i.case_price_paise), " ", h("small", { text: t("perCase") })),
            h("div", { class: "sub2", text: `${rupees(i.piece_price_paise)} ${t("perPiece")} · ${t("exGst")}` }),
            h("div", { class: "schemes" }, i.scheme_rules.map((r) => h("span", { class: "scheme", text: ruleText(r) }))),
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
  await ensureCat();
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
      h("div", { class: "row" }, h("span", { class: "name", text: itemName(l.sku_code, l.name) }), h("button", { class: "link", type: "button", onclick: () => { setQty(l.sku_code, 0); refresh(); } }, t("remove"))),
      h("div", { class: "row" }, stepper(l.sku_code, refresh), h("strong", { text: rupees(l.net_paise) })),
      l.free_cases > 0 && h("div", { class: "note", text: `+ ${l.free_cases} ${t("cases")} ${t("free")}` }),
      (itemOf(l.sku_code)?.scheme_rules || []).filter((r) => r.type === "slab" && l.cases >= r.min_cases).map((r) => h("div", { class: "note", text: ruleText(r) })),
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


// ---------- order by message (AI capture) ----------
async function viewCapture() {
  let text = state.draftText || "", err = "", busy = false;
  if (!state.cat) { try { state.cat = await api("/api/catalogue"); } catch (e) { if (e.message === "auth") return; } }
  const draw = () => {
    const ta = h("textarea", { maxlength: 2000, placeholder: "bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo…", "aria-label": t("msgTitle"), oninput: (e) => { text = e.target.value; state.draftText = text; } });
    ta.value = text;
    const btn = h("button", { class: "btn", type: "button", disabled: busy, onclick: async () => {
      if (text.trim().length < 3) { err = t("msgHint"); return draw(); }
      busy = true; err = ""; draw();
      try { const d = await api("/api/capture", { method: "POST", body: { text } }); busy = false; viewReview(d); }
      catch (e) { if (e.message === "auth") return; busy = false; err = e.offline ? t("offlineCart") : e.message; draw(); }
    } }, busy ? t("reading") : t("readOrder"));
    render(h("h2", { class: "title", text: t("msgTitle") }), h("p", { class: "hint", text: t("msgHint") }), ta,
      err && h("p", { class: "msg err", role: "alert", text: err }), h("div", { class: "field" }, btn));
  };
  draw();
}

function viewReview(d) {
  const items = state.cat?.items || [];
  const byCode = Object.fromEntries(items.map((i) => [i.code, i]));
  const rows = d.lines.map((l) => ({ id: l.line_id, orig: l.original_text, sku: l.sku_code, qty: l.qty, unit: l.unit_question ? null : l.unit || null,
    unitQ: l.unit_question, conf: l.confidence, alts: l.alternatives, note: l.note }));
  const out = (r) => !!(r.sku && byCode[r.sku] && byCode[r.sku].stock_status === "out");
  const ok = (r) => r.sku && r.qty >= 1 && r.unit && !out(r);
  let err = "", busy = false;
  const draw = () => {
    const all = rows.every(ok);
    const btn = h("button", { class: "btn", type: "button", disabled: !all || busy, onclick: async () => {
      busy = true; draw();
      try {
        const o = await api(`/api/capture/${d.id}/confirm`, { method: "POST", body: { lines: rows.map((r) => ({ line_id: r.id, sku_code: r.sku, qty: r.qty, unit: r.unit })) } });
        state.last = o; state.draftText = ""; go("#/done");
      } catch (e) {
        if (e.message === "auth") return;
        busy = false; const iss = e.data?.detail?.cart?.lines?.flatMap((l) => l.issues.map((i) => i.message));
        err = iss?.length ? iss.join(" ") : e.message; draw();
      }
    } }, t("confirmOrder"));
    render(h("h2", { class: "title", text: t("review") }),
      rows.map((r) => {
        const sel = h("select", { "aria-label": t("chooseItem"), onchange: (e) => { r.sku = e.target.value || null; draw(); } },
          h("option", { value: "" }, t("chooseItem")), items.map((i) => h("option", { value: i.code, selected: i.code === r.sku }, itemName(i.code))));
        const alts = r.alts.filter((a) => a.code !== r.sku);
        return h("div", { class: `rev${ok(r) ? "" : " bad"}` },
          h("div", { class: "quote" }, h("small", { text: t("youWrote") }), `“${r.orig}”`),
          h("span", { class: `conf ${r.conf || "low"}`, text: r.conf === "high" ? t("confHigh") : r.conf === "medium" ? t("confMed") : t("confLow") }),
          r.note && h("div", { class: "sub2", text: r.note }),
          sel,
          alts.length > 0 && h("div", { class: "alts" }, t("orPick") + ":", alts.map((a) => h("button", { class: "chip", type: "button", onclick: () => { r.sku = a.code; draw(); } }, itemName(a.code, a.name)))),
          out(r) && h("p", { class: "msg err", role: "alert", text: t("offStock") }),
          r.unitQ && !r.unit && h("p", { class: "msg warn", role: "alert", text: t("unitAsk") }),
          h("div", { class: "qrow" },
            h("label", { class: "field", style: "margin:0" }, h("span", { text: t("qty") }),
              h("input", { class: "qty", type: "number", inputmode: "numeric", min: 1, max: 10000, value: r.qty || "", oninput: (e) => { r.qty = parseInt(e.target.value, 10) || null; btn.disabled = !rows.every(ok) || busy; } })),
            h("fieldset", { class: "units" }, h("legend", { class: "sub2", text: "" }),
              [["case", t("unitCase")], ["piece", t("unitPiece")]].map(([v, label]) =>
                h("label", null, h("input", { type: "radio", name: `u${r.id}`, value: v, checked: r.unit === v, onchange: () => { r.unit = v; draw(); } }), label)))));
      }),
      err && h("p", { class: "msg err", role: "alert", text: err }),
      !all && h("p", { class: "hint", text: t("fixLines") }),
      btn, h("div", { class: "field" }, h("button", { class: "btn secondary", type: "button", onclick: viewCapture }, t("startOver"))));
  };
  draw();
}

// ---------- order history + repeat ----------
async function viewOrders() {
  render(skeleton());
  await ensureCat();
  let list;
  try { list = await api("/api/orders"); } catch (e) { if (e.message === "auth") return; return render(errorBox(e.offline ? t("offlineCart") : e.message, viewOrders)); }
  if (!list.length) return render(h("div", { class: "empty" }, h("h2", { text: t("noOrders") }), h("p", { text: t("noOrdersHint" ) }), h("button", { class: "btn sm", type: "button", onclick: () => go("#/catalogue") }, t("browse"))));
  const label = { placed: t("stPlaced"), on_hold: t("stHold"), confirmed: t("stConfirmed"), exported: t("stConfirmed"), cancelled: t("stCancelled") };
  render(h("h2", { class: "title", text: t("orders") }), list.map((o) => h("div", { class: "line" },
    h("div", { class: "row" }, h("strong", { text: `#${o.id}` }), h("span", { class: `st ${o.status}`, text: label[o.status] || o.status })),
    h("div", { class: "sub2", text: new Date(o.created_at).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) }),
    o.lines.map((l) => h("div", { class: "oline", text: `${itemName(l.sku_code, l.name)}: ${l.cases}${l.free_cases ? ` + ${l.free_cases} ${t("free")}` : ""} ${t("cases")}` })),
    h("div", { class: "row" }, h("strong", { text: rupees(o.total_paise) }),
      h("button", { class: "btn sm secondary", type: "button", onclick: async (e) => {
        e.currentTarget.disabled = true;
        try { const c = await api(`/api/orders/${o.id}/repeat`); state.cart = {}; c.lines.forEach((l) => { if (l.cases > 0) state.cart[l.sku_code] = l.cases; }); saveCart(); go("#/cart"); }
        catch (err) { if (err.message !== "auth") { e.currentTarget.disabled = false; } }
      } }, t("repeat"))))));
}


// ---------- order desk (English only: internal staff screen) ----------
const DESK_STATUS = { placed: "Placed", on_hold: "On hold", confirmed: "Confirmed", exported: "Exported", cancelled: "Cancelled" };
const stChip = (s) => h("span", { class: `st ${s}`, text: DESK_STATUS[s] || s });
const detailMsg = (e) => { const iss = e.data?.detail?.cart?.lines?.flatMap((l) => l.issues.map((i) => `${l.name}: ${i.message}`)); return iss?.length ? iss.join(" ") : e.message; };

async function exportTally(setMsg) {
  try {
    const res = await fetch("/api/desk/export/tally", { method: "POST", credentials: "same-origin" });
    if (!res.ok) { const j = await res.json().catch(() => ({})); return setMsg("err", typeof j.detail === "string" ? j.detail : "Export failed."); }
    const url = URL.createObjectURL(await res.blob());
    const a = h("a", { href: url, download: "tally-orders.csv" }); document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    setMsg("info", "Exported. Those orders are now marked Exported.");
  } catch { setMsg("err", "You are offline."); }
}

async function viewDeskQueue() {
  const filter = state.deskFilter || "";
  render(skeleton());
  let list;
  try { list = await api("/api/desk/orders" + (filter ? `?status=${filter}` : "")); }
  catch (e) { if (e.message === "auth") return; return render(errorBox(e.offline ? "You are offline." : e.message, viewDeskQueue)); }
  const box = h("div");
  const setMsg = (kind, text) => { box.replaceChildren(h("p", { class: `msg ${kind}`, role: "status", text })); if (kind === "info") setTimeout(viewDeskQueue, 800); };
  const chips = [["", "All"], ["on_hold", "On hold"], ["placed", "Placed"], ["confirmed", "Confirmed"], ["exported", "Exported"]];
  render(h("div", { class: "dhead" }, h("h2", { class: "title", style: "margin:0", text: "Order desk" }),
      h("button", { class: "btn sm", type: "button", onclick: () => exportTally(setMsg) }, "Export confirmed to Tally")), box,
    h("div", { class: "chips", role: "group" }, chips.map(([v, label]) => h("button", { class: "chip", type: "button", "aria-pressed": String(filter === v), onclick: () => { state.deskFilter = v; viewDeskQueue(); } }, label))),
    list.length === 0 ? h("div", { class: "empty" }, h("h2", { text: "No orders here" }), h("p", { text: "Nothing matches this filter." })) :
    h("div", { class: "qlist" }, list.map((o) => h("a", { class: "qrow-card", href: `#/desk/order/${o.id}` },
      h("strong", { text: `#${o.id}` }),
      h("div", {}, h("div", {}, o.shop_name, o.source === "ai_text" && h("span", { class: "ai", text: "AI message" })),
        h("div", { class: "meta", text: `${new Date(o.created_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })} · ${o.line_count} lines${o.credit_hold_reason ? " · " + o.credit_hold_reason : ""}` })),
      h("div", { class: "amt" }, stChip(o.status), h("div", { text: rupees(o.total_paise) }))))));
}

async function viewDeskOrder(id) {
  render(skeleton());
  let o, skus;
  try { [o, skus] = await Promise.all([api(`/api/desk/orders/${id}`), api("/api/desk/skus")]); }
  catch (e) { if (e.message === "auth") return; return render(h("a", { class: "back", href: "#/desk" }, "← Queue"), errorBox(e.status === 404 ? "Order not found." : e.message, () => viewDeskOrder(id))); }
  const rows = o.lines.map((l) => ({ line_id: l.line_id, sku: l.sku_code, cases: l.cases, l }));
  let dirty = false, msg = null, busy = false, note = o.desk_note || "";
  const editable = o.status === "placed" || o.status === "on_hold";
  const act = async (fn) => { if (busy) return; busy = true; msg = null; draw();
    try { const r = await fn(); busy = false; if (r) { viewDeskOrder(id); return; } } catch (e) { if (e.message === "auth") return; busy = false; msg = ["err", detailMsg(e)]; } draw(); };
  const draw = () => {
    const r = o.retailer, avail = r.credit_limit_paise - r.outstanding_paise;
    const valid = rows.length > 0 && rows.every((x) => x.sku && x.cases >= 1);
    render(h("a", { class: "back", href: "#/desk" }, "← Queue"),
      h("div", { class: "dhead" }, h("h2", { class: "title", style: "margin:0", text: `Order #${o.id}` }), stChip(o.status)),
      o.credit_hold_reason && h("p", { class: "msg warn", text: o.credit_hold_reason }),
      msg && h("p", { class: `msg ${msg[0]}`, role: "alert", text: msg[1] }),
      h("div", { class: "panel kv" },
        h("div", {}, h("b", { text: "Shop" }), r.shop_name), h("div", {}, h("b", { text: "Salesman" }), r.salesman || "-"),
        h("div", {}, h("b", { text: "Credit limit" }), rupees(r.credit_limit_paise)), h("div", {}, h("b", { text: "Outstanding" }), rupees(r.outstanding_paise)),
        h("div", {}, h("b", { text: "Available" }), rupees(avail)), h("div", {}, h("b", { text: "Order total" }), rupees(o.total_paise))),
      o.raw_text && h("div", { class: "panel" }, h("b", { class: "sub2", text: "Original message" }), h("p", { class: "quote", text: o.raw_text })),
      h("div", { class: "panel" },
        rows.map((x, i) => h("div", { class: "dline" },
          editable ? h("select", { "aria-label": "Item", onchange: (e) => { x.sku = e.target.value || null; dirty = true; draw(); } },
              h("option", { value: "" }, "Choose an item"), skus.map((k) => h("option", { value: k.code, selected: k.code === x.sku }, `${k.name}${k.stock_cases <= 0 ? " (out of stock)" : ""}`)))
            : h("span", { text: x.l.name }),
          editable ? h("input", { class: "qty", type: "number", min: 1, max: 10000, "aria-label": "Cases", value: x.cases || "", style: "width:90px",
              oninput: (e) => { x.cases = parseInt(e.target.value, 10) || 0; dirty = true; saveBtn.disabled = busy || !rows.every((y) => y.sku && y.cases >= 1); confirmBtn && (confirmBtn.disabled = true); } })
            : h("span", { text: `${x.cases} cases` }),
          h("div", { class: "amt" }, x.l.free_cases ? `+${x.l.free_cases} free · ` : "", rupees(x.l.net_paise)),
          x.l.original_text && h("div", { class: "src" }, `Retailer wrote “${x.l.original_text}”`, x.l.confidence && ` · ${x.l.confidence} confidence`),
          editable && rows.length > 1 && h("button", { class: "link", type: "button", onclick: () => { rows.splice(i, 1); dirty = true; draw(); } }, "Remove line"))),
        editable && h("button", { class: "link", type: "button", onclick: () => { rows.push({ line_id: null, sku: null, cases: 1, l: {} }); dirty = true; draw(); } }, "+ Add item")),
      editable && h("label", { class: "field" }, h("span", { text: "Desk note (shown in the Tally narration)" }), h("input", { maxlength: 300, value: note, oninput: (e) => { note = e.target.value; dirty = true; } })),
      dirty && h("p", { class: "hint", text: "You have unsaved changes. Save before confirming." }),
      h("div", { class: "actions" }, editable && saveBtn, o.status === "on_hold" && releaseBtn, o.status === "placed" && confirmBtn, editable && cancelBtn));
    saveBtn.disabled = busy || !dirty || !valid; releaseBtn.disabled = busy || dirty; confirmBtn.disabled = busy || dirty; cancelBtn.disabled = busy;
  };
  const saveBtn = h("button", { class: "btn", type: "button", onclick: () => act(async () => { await api(`/api/desk/orders/${id}/lines`, { method: "PUT", body: { note, lines: rows.map((x) => ({ line_id: x.line_id, sku_code: x.sku, cases: x.cases })) } }); return true; }) }, "Save changes");
  const releaseBtn = h("button", { class: "btn secondary", type: "button", onclick: () => act(async () => { await api(`/api/desk/orders/${id}/release`, { method: "POST", body: { note } }); return true; }) }, "Release credit hold");
  const confirmBtn = h("button", { class: "btn", type: "button", onclick: () => act(async () => { await api(`/api/desk/orders/${id}/confirm`, { method: "POST" }); return true; }) }, "Confirm order");
  const cancelBtn = h("button", { class: "btn danger", type: "button", onclick: () => { if (confirm("Cancel this order?")) act(async () => { await api(`/api/desk/orders/${id}/cancel`, { method: "POST", body: { note } }); return true; }); } }, "Cancel order");
  draw();
}

// ---------- routing ----------
function route() {
  const r = location.hash || "#/catalogue";
  if (!state.me) return r === "#/login" ? viewLogin() : go("#/login");
  if (state.me.role === "desk") {
    const m = r.match(/^#\/desk\/order\/(\d+)$/);
    if (m) return viewDeskOrder(Number(m[1]));
    if (r !== "#/desk") return go("#/desk");
    return viewDeskQueue();
  }
  if (r === "#/cart") return viewCart();
  if (r === "#/done") return viewDone();
  if (r === "#/message") return viewCapture();
  if (r === "#/orders") return viewOrders();
  if (r === "#/login") return go("#/catalogue");
  return viewCatalogue();
}
async function boot() {
  try { state.me = await api("/api/me"); } catch { state.me = null; }
  if (state.me && (!location.hash || location.hash === "#/login")) location.hash = state.me.role === "desk" ? "#/desk" : "#/catalogue";
  route();
}
state.lang = store.get("lang") === "hi" ? "hi" : "en"; document.documentElement.lang = state.lang;
loadCart();
window.addEventListener("hashchange", route);
if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
boot();
})();
