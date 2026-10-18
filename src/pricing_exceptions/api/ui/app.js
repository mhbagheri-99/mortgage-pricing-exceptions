// The page for pricing exception requests (design doc D11, 6.6). It calls only the public API and
// /demo/users, and holds no business rule: every refusal it shows comes from the server, word for word.
// Text from the server is only ever set as text, through textContent or append() with a string, so a reason
// that looks like markup stays text (design doc 7.5).

const REQUESTS = "/api/v1/pricing-exception-requests";
// sessionStorage belongs to one tab, so two tabs can act as two users (the four-eyes and stale-revision demos).
const IDENTITY_KEY = "pricing-exceptions.acting-as";

const STATUS = { PENDING_REVIEW: "Pending review", APPROVED: "Approved", DECLINED: "Declined" };
const ROLES = {
  RELATIONSHIP_MANAGER: "relationship manager",
  REVIEWER: "reviewer",
  MORTGAGE_PROCESS: "mortgage process",
};
const EVENTS = {
  SUBMITTED: "Submitted",
  AMENDED: "Amended",
  RESUBMITTED: "Resubmitted",
  APPROVED: "Approved",
  DECLINED: "Declined",
};
// The actions the server reports in `permissions` (P7), named as the page offers them.
const ACTIONS = { decide: "Approve or decline", revise: "Change the terms" };
const FIELDS = {
  application_id: "Application id",
  discount_bps: "Discount",
  reason: "Reason",
  comment: "Comment",
  revision: "Revision",
  outcome: "Outcome",
  status: "Status",
  limit: "Per page",
  cursor: "Cursor",
};

const state = { me: readIdentity(), status: "PENDING_REVIEW", limit: 10, nextCursor: null, shownId: null };
const $ = (id) => document.getElementById(id);

function readIdentity() {
  try {
    return sessionStorage.getItem(IDENTITY_KEY) || "";
  } catch {
    return ""; // storage blocked: the choice lasts until the page reloads
  }
}

function writeIdentity(id) {
  try {
    sessionStorage.setItem(IDENTITY_KEY, id);
  } catch {
    // storage blocked: nothing to remember the choice in
  }
}

// One element; `text` is set through textContent, children are nodes or plain strings (which become text).
function el(tag, options = {}, ...children) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = String(options.text);
  for (const [name, value] of Object.entries(options.attrs || {})) node.setAttribute(name, value);
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== false));
  return node;
}

const percent = (bps) => (bps / 100).toFixed(2);
const discount = (bps) => `${bps} bps (${percent(bps)}%)`;
// The API's UTC times, shown as UTC, so they match the history and the logs.
const when = (iso) => iso.replace("T", " ").replace(/\.\d+/, "").replace(/(Z|\+00:00)$/, " UTC");
const day = (iso) => iso.slice(0, 10);
const statusLabel = (status) => STATUS[status] || status;
// A whole number as a number; anything else goes to the server as typed, and the server answers 422.
const whole = (text) => (/^\s*-?\d+\s*$/.test(text) ? Number(text) : text);

function badge(status) {
  const known = status in STATUS ? status.toLowerCase().replace("_", "-") : "unknown";
  return el("span", { className: `badge badge-${known}`, text: statusLabel(status) });
}

function button(text, onClick, className = "") {
  const node = el("button", { className, attrs: { type: "button" }, text });
  node.addEventListener("click", onClick);
  return node;
}

function field(label, control) {
  return el("label", {}, label, control);
}

async function call(method, path, body) {
  const headers = { Accept: "application/json" };
  if (state.me) headers["X-User-Id"] = state.me;
  const init = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch {
    return {
      ok: false,
      status: 0,
      data: { title: "The service did not answer", detail: "Check that it is running, then try again." },
    };
  }
  const data = await response.json().catch(() => ({}));
  const replayed = response.headers.get("Idempotent-Replayed") === "true";
  return { ok: response.ok, status: response.status, data, replayed };
}

// ---- Messages ------------------------------------------------------------------------------------------

function summary(current) {
  const status = statusLabel(current.status).toLowerCase();
  const parts = `revision ${current.revision}, ${discount(current.discount_bps)}, ${status}`;
  const decision = current.latest_decision;
  if (!decision) return `${parts}.`;
  return `${parts}; ${statusLabel(decision.outcome).toLowerCase()} by ${decision.decided_by} on revision ${decision.revision}.`;
}

// A refusal as the server sent it: status and title, detail, each field error, and what is current now.
function renderProblem(target, result, buttons = []) {
  const problem = result.data || {};
  const box = el(
    "div",
    { className: "problem", attrs: { role: "alert" } },
    el(
      "p",
      { className: "problem-title" },
      result.status ? el("span", { className: "code", text: result.status }) : null,
      problem.title || "Refused",
    ),
    problem.detail ? el("p", { text: problem.detail }) : null,
  );
  if (Array.isArray(problem.errors) && problem.errors.length) {
    const where = (error) => {
      const name = error.pointer ? error.pointer.replace(/^#\//, "") : error.parameter || "";
      return FIELDS[name] || name;
    };
    box.append(el("ul", {}, ...problem.errors.map((error) => el("li", { text: `${where(error)}: ${error.detail}` }))));
  }
  if (problem.current) box.append(el("p", { className: "current", text: `Now: ${summary(problem.current)}` }));
  if (buttons.length) box.append(el("div", { className: "buttons" }, ...buttons));
  target.replaceChildren(box);
  box.scrollIntoView({ block: "nearest" });
}

// ---- Acting as -----------------------------------------------------------------------------------------

async function start() {
  wireEvents();
  showWelcome();
  const result = await call("GET", "/demo/users");
  if (!result.ok) {
    const note = $("identity-note");
    note.textContent =
      result.status === 404
        ? "The demo identity is off, so this page cannot act as anyone. Start the service with DEMO_IDENTITY=true."
        : `The user list did not load (${result.status || "no answer"}).`;
    note.hidden = false;
    $("acting-as").disabled = true;
    $("new-request").disabled = true;
    clearList("Nobody to act as.");
    return;
  }
  for (const user of result.data) {
    const roles = user.roles.map((role) => ROLES[role] || role).join(", ");
    $("acting-as").append(
      el("option", { attrs: { value: user.id, title: user.display_name }, text: `${user.id} (${roles})` }),
    );
  }
  if (!result.data.some((user) => user.id === state.me)) state.me = "";
  $("acting-as").value = state.me;
  await actAs(state.me);
}

async function actAs(id) {
  state.me = id;
  writeIdentity(id);
  if (!id) {
    clearList("Choose who you are acting as, in the top right.");
    showWelcome();
    return;
  }
  await loadList();
  if (state.shownId) await openRequest(state.shownId);
  else showNothingOpen();
}

// ---- The queue -----------------------------------------------------------------------------------------

function clearList(message) {
  $("rows").replaceChildren();
  $("list-problem").replaceChildren();
  state.nextCursor = null;
  $("load-more").hidden = true;
  $("list-empty").textContent = message;
  $("list-empty").hidden = false;
}

// Oldest first, one page at a time; "Load more" follows next_cursor until it is null (design doc 5.6).
async function loadList(more = false) {
  const params = new URLSearchParams({ status: state.status, limit: String(state.limit) });
  if (more && state.nextCursor) params.set("cursor", state.nextCursor);
  const result = await call("GET", `${REQUESTS}?${params}`);
  $("list-problem").replaceChildren();
  if (!result.ok) {
    if (!more) $("rows").replaceChildren();
    state.nextCursor = null;
    $("load-more").hidden = true;
    $("list-empty").hidden = true;
    renderProblem($("list-problem"), result);
    return;
  }
  const rows = result.data.items.map(row);
  if (more) $("rows").append(...rows);
  else $("rows").replaceChildren(...rows);
  state.nextCursor = result.data.next_cursor;
  $("load-more").hidden = !state.nextCursor;
  $("list-empty").textContent = `No request is ${statusLabel(state.status).toLowerCase()}.`;
  $("list-empty").hidden = $("rows").children.length > 0;
  markSelected();
}

function row(item) {
  const open = el(
    "button",
    { className: "row", attrs: { type: "button", "data-id": item.id } },
    el("span", { className: "row-app", text: item.application_id }),
    el("span", { className: "row-bps", text: `${item.discount_bps} bps` }),
    el("span", { className: "row-meta", text: `Revision ${item.revision}, ${item.requested_by}` }),
    el("span", { className: "row-date", text: day(item.created_at) }),
  );
  open.addEventListener("click", () => openRequest(item.id));
  return el("li", {}, open);
}

function markSelected() {
  for (const open of $("rows").querySelectorAll("button.row")) {
    if (open.dataset.id === state.shownId) open.setAttribute("aria-current", "true");
    else open.removeAttribute("aria-current");
  }
}

function selectStatus(tab) {
  state.status = tab.dataset.status;
  for (const other of document.querySelectorAll(".status-tabs button")) {
    other.setAttribute("aria-pressed", String(other === tab));
  }
  loadList();
}

// ---- Create --------------------------------------------------------------------------------------------

function openCreate() {
  $("create-result").replaceChildren();
  $("create-dialog").showModal();
  $("create-form").elements.application_id.focus();
}

async function create(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const result = await call("POST", REQUESTS, {
    application_id: form.elements.application_id.value,
    discount_bps: whole(form.elements.discount_bps.value),
    reason: form.elements.reason.value,
  });
  if (result.ok) {
    $("create-dialog").close();
    // The form keeps what was sent: sending it again shows the replay, never a second request.
    const done = result.replayed
      ? "Already created: the same request came back as a replay, not a second request."
      : `Created the request for ${result.data.application_id}.`;
    renderRequest(result.data, done);
    await loadList();
    return;
  }
  const buttons = [];
  if (result.status === 409 && result.data.current) {
    const existing = result.data.current.id;
    buttons.push(
      button("Open the existing request", () => {
        $("create-dialog").close();
        openRequest(existing);
      }),
    );
  }
  renderProblem($("create-result"), result, buttons);
}

// ---- The case and its actions --------------------------------------------------------------------------

function showCase(...parts) {
  $("case").replaceChildren(...parts.filter((part) => part !== null)); // a null would show as "null"
}

function showActions(...parts) {
  const shown = parts.filter((part) => part !== null);
  $("actions").replaceChildren(...shown);
  $("actions").hidden = shown.length === 0;
  $("detail").classList.toggle("with-actions", shown.length > 0);
}

function emptyState(title, text) {
  return el(
    "div",
    { className: "empty-state" },
    el("h2", { attrs: { id: "case-title" }, text: title }),
    el("p", { text }),
  );
}

function showWelcome() {
  state.shownId = null;
  showCase(
    emptyState(
      "Choose who you are acting as",
      "Pick a user in the top right. Each browser tab remembers its own choice, so a second tab can act as " +
        "someone else: the requester in one, a reviewer in the other.",
    ),
  );
  showActions();
  markSelected();
}

function showNothingOpen() {
  state.shownId = null;
  showCase(emptyState("No request open", "Open one from the list on the left, or create a new request."));
  showActions();
  markSelected();
}

async function openRequest(id, keepTyped = false) {
  const typed = keepTyped ? readTyped() : [];
  const result = await call("GET", `${REQUESTS}/${encodeURIComponent(id)}`);
  if (!result.ok) {
    state.shownId = id; // kept, so switching to a user who may read it opens it again
    const slot = el("div");
    showCase(el("h2", { attrs: { id: "case-title" }, text: "Request" }), slot);
    showActions();
    renderProblem(slot, result);
    markSelected();
    return;
  }
  renderRequest(result.data);
  restoreTyped(typed);
}

function renderRequest(record, notice = "") {
  state.shownId = record.id;
  const decision = record.latest_decision;
  showCase(
    el(
      "header",
      { className: "case-head" },
      el(
        "div",
        { className: "case-title" },
        el("h2", { attrs: { id: "case-title" }, text: record.application_id }),
        badge(record.status),
      ),
      // Every rule in this service binds to a revision, so the revision on screen is the page's loud element.
      el(
        "p",
        { className: "stamp", attrs: { "aria-label": `Revision ${record.revision}` } },
        el("span", { className: "stamp-label", text: "Revision" }),
        el("span", { className: "stamp-number", text: record.revision }),
      ),
    ),
    el(
      "p",
      { className: "terms" },
      el("span", { className: "terms-bps", text: `${record.discount_bps} bps` }),
      el("span", { className: "terms-percent", text: `${percent(record.discount_bps)}% off the standard rate` }),
    ),
    el("p", { className: "requested", text: `Requested by ${record.requested_by} on ${when(record.created_at)}` }),
    el("blockquote", { className: "reason", text: record.reason }),
    el(
      "dl",
      { className: "facts" },
      el("dt", { text: "Latest decision" }),
      el("dd", { text: decision ? decisionText(decision) : "None yet" }),
      el("dt", { text: "Request id" }),
      el("dd", { className: "request-id", text: record.id }),
    ),
    el(
      "section",
      { className: "history", attrs: { "aria-labelledby": "history-title" } },
      el("h3", { attrs: { id: "history-title" }, text: "History" }),
      timeline(record.history),
    ),
  );
  // The server says what this caller may do (`permissions`, P7): only those forms are offered, and every
  // other action is listed with the server's own reason. The server still checks each rule when it is tried.
  const allowed = record.permissions || {};
  const refused = Object.entries(allowed).filter(([, permission]) => !permission.allowed);
  showActions(
    notice ? el("p", { className: "notice", attrs: { role: "status" }, text: notice }) : null,
    record.status === "APPROVED"
      ? el("p", { className: "final", text: "Approved is final. The mortgage process can now read this discount." })
      : null,
    allowed.decide?.allowed ? decideForm(record) : null,
    allowed.revise?.allowed ? reviseForm(record) : null,
    refused.length ? notAvailable(refused) : null,
  );
  markSelected();
}

function notAvailable(refused) {
  return el(
    "section",
    { className: "action-card unavailable", attrs: { "aria-labelledby": "unavailable-title" } },
    el("h3", { attrs: { id: "unavailable-title" }, text: "Not available to you" }),
    el(
      "ul",
      { className: "refusals" },
      ...refused.map(([action, permission]) =>
        el(
          "li",
          {},
          el("span", { className: "refusal-action", text: ACTIONS[action] || action }),
          el("span", { className: "refusal-detail", text: permission.detail || "" }),
        ),
      ),
    ),
  );
}

function decisionText(decision) {
  const what = `${statusLabel(decision.outcome)} by ${decision.decided_by} at ${when(decision.decided_at)}, on revision ${decision.revision}.`;
  return decision.comment ? `${what} Comment: ${decision.comment}` : what;
}

function timeline(events) {
  const kind = (type) => (type === "APPROVED" ? "approved" : type === "DECLINED" ? "declined" : "change");
  return el(
    "ol",
    { className: "timeline" },
    ...events.map((event) =>
      el(
        "li",
        { className: `event event-${kind(event.event_type)}` },
        el(
          "p",
          { className: "event-title" },
          el("span", { className: "event-name", text: EVENTS[event.event_type] || event.event_type }),
          el("span", { className: "event-revision", text: `revision ${event.revision}` }),
        ),
        el("p", { className: "event-meta", text: `${event.actor}, ${when(event.occurred_at)}` }),
        el("p", { className: "event-terms", text: discount(event.discount_bps) }),
        el("p", { className: "event-reason", text: event.reason }),
        event.comment ? el("p", { className: "event-comment", text: event.comment }) : null,
      ),
    ),
  );
}

// Offered only when the server's `permissions` allow it for this caller (P7).
function decideForm(record) {
  const form = el(
    "form",
    { className: "action-card decide", attrs: { id: "decide-form", novalidate: "" } },
    el("h3", { text: `Approve or decline revision ${record.revision}` }),
    el("p", {
      className: "binds",
      text:
        `Your decision applies to revision ${record.revision} only: ${discount(record.discount_bps)} and the ` +
        "reason shown. If the request changes first, you are asked to reload.",
    }),
    field("Comment (needed to decline)", el("textarea", { attrs: { name: "comment", rows: "3" } })),
  );
  const slot = el("div"); // a refusal shows here, under the buttons
  const decide = async (outcome) => {
    const comment = form.elements.comment.value;
    // The revision on screen, never a fresh read: an approval binds to the terms the reviewer saw. If the
    // request changed since, the server answers 409 stale-revision instead of approving other terms.
    const body = { revision: record.revision, outcome };
    if (comment !== "") body.comment = comment; // sent exactly as typed; the server checks it
    const result = await call("POST", `${REQUESTS}/${record.id}/decisions`, body);
    await afterWrite(result, (changed) => `${statusLabel(outcome)}: revision ${changed.revision}.`, slot);
  };
  form.addEventListener("submit", (event) => event.preventDefault());
  form.append(
    el(
      "div",
      { className: "buttons" },
      button("Approve", () => decide("APPROVED"), "approve"),
      button("Decline", () => decide("DECLINED"), "decline"),
    ),
    slot,
  );
  return form;
}

function reviseForm(record) {
  const declined = record.status === "DECLINED";
  const form = el(
    "form",
    { className: "action-card", attrs: { id: "revise-form", novalidate: "" } },
    el("h3", { text: declined ? "Resubmit with new terms" : "Change the terms" }),
    el("p", {
      className: "who",
      text: declined
        ? `New terms become revision ${record.revision + 1} and go back to a reviewer.`
        : `Your change becomes revision ${record.revision + 1}, and a reviewer decides again.`,
    }),
    field(
      "Discount in basis points",
      el("input", {
        attrs: { name: "discount_bps", inputmode: "numeric", autocomplete: "off", value: String(record.discount_bps) },
      }),
    ),
    field("Reason", el("textarea", { attrs: { name: "reason", rows: "3" }, text: record.reason })),
    el(
      "div",
      { className: "buttons" },
      el("button", { attrs: { type: "submit" }, text: declined ? "Resubmit" : "Send changes" }),
    ),
  );
  const slot = el("div"); // a refusal shows here, under the button that caused it
  form.append(slot);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = await call("POST", `${REQUESTS}/${record.id}/revisions`, {
      revision: record.revision, // the revision on screen, as for a decision
      discount_bps: whole(form.elements.discount_bps.value),
      reason: form.elements.reason.value,
    });
    await afterWrite(result, (changed) => `Sent: the request is now at revision ${changed.revision}.`, slot);
  });
  return form;
}

// After a write: success redraws the request from the answer, which has the GET shape. A refusal is shown
// under the form that sent it and changes nothing else on screen; a 409 also offers to reload (P7 decision 1).
async function afterWrite(result, done, slot) {
  if (result.ok) {
    renderRequest(result.data, done(result.data));
    await loadList();
    return;
  }
  const buttons = result.status === 409 ? [button("Reload request", () => openRequest(state.shownId, true))] : [];
  renderProblem(slot, result, buttons);
}

// What the user changed in the two forms, so a reload after a 409 keeps it. Only edited fields: an untouched
// field still holds the old revision's terms, and restoring it would quietly send them back (P7 review).
function readTyped() {
  const typed = [];
  for (const id of ["revise-form", "decide-form"]) {
    const form = $(id);
    if (!form) continue;
    for (const control of form.elements) {
      if (control.name && control.value !== control.defaultValue) typed.push([id, control.name, control.value]);
    }
  }
  return typed;
}

function restoreTyped(typed) {
  for (const [id, name, value] of typed) {
    const control = $(id)?.elements.namedItem(name);
    if (control) control.value = value;
  }
}

function wireEvents() {
  $("acting-as").addEventListener("change", (event) => actAs(event.target.value));
  for (const tab of document.querySelectorAll(".status-tabs button")) {
    tab.addEventListener("click", () => selectStatus(tab));
  }
  $("limit").addEventListener("change", (event) => {
    state.limit = Number(event.target.value);
    loadList();
  });
  $("load-more").addEventListener("click", () => loadList(true));
  $("new-request").addEventListener("click", openCreate);
  $("create-cancel").addEventListener("click", () => $("create-dialog").close());
  $("create-form").addEventListener("submit", create);
}

start();
