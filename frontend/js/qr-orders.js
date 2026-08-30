/* Live Orders board — staff screen. Polls every 5s (no WebSockets in the stack). */

const NEXT_STATUS = {
  NEW: "ACCEPTED",
  ACCEPTED: "PREPARING",
  PREPARING: "READY",
  READY: "SERVED",
};
const POLL_MS = 5000;
let pollTimer = null;
let lastSignature = "";

async function boot() {
  const user = await requireAuth();
  if (!user) return;
  renderSidebar("qr-orders", user);
  document.getElementById("refreshBtn").addEventListener("click", () => load(true));
  document.getElementById("statusFilter").addEventListener("change", () => load(true));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopPoll(); else startPoll();
  });
  await load(true);
  startPoll();
}

function startPoll() {
  stopPoll();
  pollTimer = setInterval(() => load(false), POLL_MS);
}
function stopPoll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

async function load(showErrors) {
  const status = document.getElementById("statusFilter").value;
  const qs = status === "active" ? "?scope=active" : (status ? `?status=${status}` : "");
  let data;
  try {
    data = await apiFetch("/qr-ordering/orders" + qs);
  } catch (e) {
    if (showErrors) showToast(e.message, true);
    return;
  }
  render(data.orders || []);
}

function render(orders) {
  const signature = orders.map((o) => `${o.id}:${o.status}`).join("|");
  if (signature === lastSignature) return;   // nothing changed, skip DOM churn
  lastSignature = signature;

  const board = document.getElementById("ordersBoard");
  document.getElementById("ordersEmpty").style.display = orders.length ? "none" : "block";

  board.innerHTML = orders.map((o) => {
    const lines = o.items.map((it) => `
      <li><span>${escapeHtml(it.item_name)}${it.brand ? " · " + escapeHtml(it.brand) : ""} × ${it.qty}</span>
          <span>${formatMoney(it.line_total)}</span></li>`).join("");
    const advance = NEXT_STATUS[o.status];
    const canCancel = o.status !== "SERVED" && o.status !== "CANCELLED";
    return `
      <div class="order-card" data-status="${o.status}">
        <div class="oc-head">
          <span class="oc-table">${escapeHtml(o.table_label)}</span>
          <span class="oc-no">${escapeHtml(o.order_no)}</span>
        </div>
        <span class="oc-status-pill">${o.status}${o.pushed_to_bill ? " · on bill" : ""}</span>
        <ul class="oc-lines">${lines}</ul>
        <div class="oc-lines"><li><span>Subtotal</span><span>${formatMoney(o.subtotal)}</span></li>
          ${Number(o.tax) ? `<li><span>Tax</span><span>${formatMoney(o.tax)}</span></li>` : ""}</div>
        <div class="oc-total"><span>Total</span> <span style="float:right">${formatMoney(o.grand_total)}</span></div>
        ${o.note ? `<div class="oc-note">Note: ${escapeHtml(o.note)}</div>` : ""}
        <div class="oc-actions">
          ${advance ? `<button class="btn btn-primary" data-advance="${o.id}" data-to="${advance}">Mark ${advance}</button>` : ""}
          ${!o.pushed_to_bill && o.status !== "CANCELLED" ? `<button class="btn btn-outline" data-push="${o.id}">Add to bill</button>` : ""}
          ${canCancel ? `<button class="btn btn-outline" data-cancel="${o.id}">Cancel</button>` : ""}
        </div>
      </div>`;
  }).join("");

  board.querySelectorAll("[data-advance]").forEach((b) =>
    b.addEventListener("click", () => setStatus(Number(b.dataset.advance), b.dataset.to)));
  board.querySelectorAll("[data-cancel]").forEach((b) =>
    b.addEventListener("click", () => {
      if (confirm("Cancel this order?")) setStatus(Number(b.dataset.cancel), "CANCELLED");
    }));
  board.querySelectorAll("[data-push]").forEach((b) =>
    b.addEventListener("click", () => pushToBill(Number(b.dataset.push))));
}

async function setStatus(id, status) {
  try {
    await apiFetch(`/qr-ordering/orders/${id}/status`, { method: "POST", body: { status } });
    lastSignature = "";
    await load(true);
  } catch (e) {
    showToast(e.message, true);
  }
}

async function pushToBill(id) {
  try {
    await apiFetch(`/qr-ordering/orders/${id}/push-to-bill`, { method: "POST" });
    showToast("Order added to the table bill");
    lastSignature = "";
    await load(true);
  } catch (e) {
    showToast(e.message, true);
  }
}

boot();
