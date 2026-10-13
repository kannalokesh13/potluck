/**
 * Potluck board UI: status board, add/claim/unclaim, people, share link.
 *
 * All user-supplied text (titles, names, URLs) is written with textContent or
 * validated property assignment. innerHTML is never used.
 * @module app
 */
import { boardIdFromLocation, createApi, ApiError } from "./api.js";
import { BoardClient, derivePeople } from "./realtime.js";

const NAME_KEY = "potluck.name";
const TABS = ["items", "people", "share"];
const TOAST_MS = 3500;
const ERROR_MS = 8000;

const boardId = boardIdFromLocation();
if (!boardId) {
  window.location.replace("/");
} else {
  init(boardId);
}

/* ---------------------------------------------------------------------------
 * Small helpers
 * ------------------------------------------------------------------------- */

/** Create an element with optional class and text (text is set safely). */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function normName(value) {
  return String(value || "").trim().toLowerCase();
}

function isTaken(item) {
  return Boolean(item.claimed_by && String(item.claimed_by).trim());
}

/** Return the URL only if it is a plain http(s) URL, otherwise null. */
function safeImageUrl(value) {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!/^https?:\/\//i.test(trimmed)) return null;
  try {
    const url = new URL(trimmed);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

const EMOJI_RULES = [
  [/biryani|rice|pulao|pulav/, "\u{1F35A}"],
  [/snack|samosa|pakora|pakoda|chips|fries|cutlet/, "\u{1F95F}"],
  [/sweet|jamun|halwa|laddu|ladoo|dessert|cake|kheer|payasam|barfi/, "\u{1F36C}"],
  [/drink|juice|lassi|tea|coffee|soda|water|cola|buttermilk/, "\u{1F964}"],
  [/salad/, "\u{1F957}"],
];
const DEFAULT_EMOJI = "\u{1F37D}\uFE0F";

function emojiFor(title) {
  const t = String(title || "").toLowerCase();
  for (const [pattern, emoji] of EMOJI_RULES) {
    if (pattern.test(t)) return emoji;
  }
  return DEFAULT_EMOJI;
}

function makePlaceholder(title) {
  const div = el("div", "item-image placeholder", emojiFor(title));
  div.setAttribute("aria-hidden", "true");
  return div;
}

function reducedMotion() {
  return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function scrollToEl(node) {
  node.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
}

function readStoredName() {
  try {
    return window.localStorage.getItem(NAME_KEY) || "";
  } catch {
    return "";
  }
}

function storeName(value) {
  try {
    window.localStorage.setItem(NAME_KEY, value);
  } catch {
    /* storage unavailable (private mode): the name just won't persist */
  }
}

/** Turn any thrown value into a friendly message (claim/unclaim/add). */
function errorMessage(err) {
  if (err instanceof ApiError) {
    if (err.status === 409 && err.currentClaimer) {
      return `Already taken by ${err.currentClaimer}`;
    }
    return err.message;
  }
  return "Something went wrong. Please try again.";
}

/** Friendly message for a failed delete (never uses the "Already taken by" wording). */
function deleteErrorMessage(err) {
  if (err instanceof ApiError) {
    if (err.status === 403) return "That employee ID doesn't match this item.";
    if (err.status === 404) return "This item no longer exists.";
    // 409 carries the server's "the bringer must release it first" text; the
    // timeout (status 0) and network messages are already user friendly.
    return err.message;
  }
  return "Something went wrong. Please try again.";
}

function hasText(value) {
  return typeof value === "string" && value.trim() !== "";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * Format a stored "YYYY-MM-DD" as "D Mon YYYY" by manual parsing (never
 * `new Date(...)`, so the viewer's time zone cannot shift the day).
 * @param {unknown} iso
 * @returns {string|null} null when missing or invalid.
 */
export function formatBringDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(typeof iso === "string" ? iso : "");
  if (!m) return null;
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return `${day} ${MONTHS[month - 1]} ${m[1]}`;
}

/** Strict calendar check for "YYYY-MM-DD" (no Date object, no time zones). */
function isValidIsoDate(value) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(typeof value === "string" ? value : "");
  if (!m) return false;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (year < 1 || month < 1 || month > 12 || day < 1) return false;
  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  return day <= days;
}

/** Length in characters (code points), matching how the server counts. */
function charLength(value) {
  return Array.from(String(value)).length;
}

const LIMITS = { name: 60, employeeId: 30, title: 100 };

/* ---------------------------------------------------------------------------
 * Main
 * ------------------------------------------------------------------------- */

function init(id) {
  const $ = (elementId) => document.getElementById(elementId);

  const statusEl = $("connection-status");
  const nameInput = $("my-name");
  const nameForm = $("name-form");
  const nameError = $("name-error");
  const itemsSummary = $("items-summary");
  const itemsError = $("items-error");
  const itemsEmpty = $("items-empty");
  const itemsList = $("items-list");
  const addForm = $("add-form");
  const addNameInput = $("add-name");
  const employeeInput = $("add-employee-id");
  const titleInput = $("item-title");
  const dateInput = $("item-date");
  const imageInput = $("item-image");
  const addError = $("add-error");
  const addSubmit = $("add-submit");
  const peopleMeta = $("people-meta");
  const peopleEmpty = $("people-empty");
  const peopleList = $("people-list");
  const shareInput = $("share-link");
  const copyBtn = $("copy-link");
  const webShareBtn = $("web-share");
  const shareError = $("share-error");
  const toastRegion = $("toast-region");
  const tabButtons = TABS.map((name) => $(`tab-${name}`));

  const api = createApi(id);

  /** @type {Array<Object>} latest items from the live client */
  let items = [];
  /** In-flight "<itemId>:<action>" keys, used to disable buttons. */
  const pending = new Set();
  /** URLs whose image failed to load; skip them on later renders. */
  const badImages = new Set();
  /** Focus to restore after a render when focus was lost to a disabled button. */
  let restoreFocus = null;
  /**
   * Open delete confirmation, or null. Kept here (not in the DOM) because every
   * realtime event rebuilds the cards. The typed id lives only in this variable
   * and the input; it is never persisted.
   * @type {{itemId: string, value: string, error: string, busy: boolean}|null}
   */
  let deletePanel = null;
  let deleteDoneTimer = null;
  let toastTimer = null;
  let itemsErrorTimer = null;

  /* ---- Messages ---------------------------------------------------------- */

  function showToast(message) {
    clearTimeout(toastTimer);
    const toast = el("div", "toast", message);
    toastRegion.replaceChildren(toast);
    toastTimer = setTimeout(() => toastRegion.replaceChildren(), TOAST_MS);
  }

  function setItemsError(message) {
    clearTimeout(itemsErrorTimer);
    itemsError.textContent = message || "";
    if (message) {
      itemsErrorTimer = setTimeout(() => {
        itemsError.textContent = "";
      }, ERROR_MS);
    }
  }

  /* ---- Connection status ------------------------------------------------- */

  function onStatus(status) {
    statusEl.classList.remove("online", "offline");
    if (status === "online") {
      statusEl.classList.add("online");
      statusEl.textContent = "Live";
    } else if (status === "offline") {
      statusEl.classList.add("offline");
      statusEl.textContent = "Reconnecting...";
    } else {
      statusEl.textContent = "Connecting...";
    }
  }

  /* ---- Rendering --------------------------------------------------------- */

  function renderMedia(item) {
    const url = safeImageUrl(item.image_url);
    if (!url || badImages.has(url)) return makePlaceholder(item.title);
    const img = el("img", "item-image");
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = "";
    img.referrerPolicy = "no-referrer";
    img.addEventListener("error", () => {
      badImages.add(url);
      if (img.isConnected) img.replaceWith(makePlaceholder(item.title));
    });
    img.src = url;
    return img;
  }

  function actionButton(item, action) {
    const btn = el("button", "btn btn-small");
    btn.type = "button";
    btn.dataset.action = action;
    btn.dataset.itemId = item.id;
    if (action === "claim") {
      btn.classList.add("btn-primary");
      btn.textContent = "I'll bring this";
      btn.setAttribute("aria-label", `I'll bring this: ${item.title}`);
    } else if (action === "delete") {
      btn.classList.add("btn-danger-ghost");
      btn.textContent = "Delete";
      btn.setAttribute("aria-label", `Delete ${item.title}`);
    } else {
      btn.classList.add("btn-danger-ghost");
      btn.textContent = "I can't bring it";
      btn.setAttribute("aria-label", `I can't bring ${item.title}`);
    }
    if (pending.has(`${item.id}:${action}`)) btn.disabled = true;
    return btn;
  }

  /** "Added by", "Employee ID" and date parts; missing parts are omitted entirely. */
  function renderMeta(item) {
    const parts = [];
    const labelled = (label, value) => {
      const span = el("span", "meta-part");
      span.appendChild(el("span", "meta-label", label));
      span.appendChild(document.createTextNode(" "));
      span.appendChild(el("span", "meta-value", String(value).trim()));
      return span;
    };
    if (hasText(item.added_by)) parts.push(labelled("Added by", item.added_by));
    if (hasText(item.employee_id)) parts.push(labelled("Employee ID", item.employee_id));
    const dateText = formatBringDate(item.bring_date);
    if (dateText) {
      const time = el("time", "meta-date", dateText);
      time.setAttribute("datetime", item.bring_date);
      parts.push(time);
    }
    if (parts.length === 0) return null;

    const p = el("p", "item-meta");
    parts.forEach((part, index) => {
      if (index > 0) {
        const sep = el("span", "meta-sep", "\u00B7");
        sep.setAttribute("aria-hidden", "true");
        p.appendChild(sep);
      }
      p.appendChild(part);
    });
    return p;
  }

  function isLegacyItem(item) {
    return !hasText(item.added_by) && !hasText(item.bring_date);
  }

  /** Inline delete confirmation (a <form>, so Enter confirms). */
  function renderDeletePanel(item, panel) {
    const form = el("form", "delete-confirm");
    form.noValidate = true;
    form.dataset.itemId = item.id;
    const promptId = `delete-prompt-${item.id}`;
    const inputId = `delete-eid-${item.id}`;
    const hintId = `delete-hint-${item.id}`;
    const errorId = `delete-error-${item.id}`;
    form.setAttribute("aria-labelledby", promptId);

    const prompt = el("p", "delete-prompt", `Delete "${String(item.title)}"?`);
    prompt.id = promptId;
    form.appendChild(prompt);

    const field = el("div", "field");
    const label = el("label", null, "Employee ID");
    label.htmlFor = inputId;
    field.appendChild(label);

    const input = el("input");
    input.type = "text";
    input.id = inputId;
    input.maxLength = LIMITS.employeeId;
    input.autocomplete = "off";
    input.value = panel.value;
    input.readOnly = panel.busy;
    input.dataset.action = "delete-input";
    input.dataset.itemId = item.id;
    input.setAttribute("aria-describedby", `${hintId} ${errorId}`);
    field.appendChild(input);

    let hint = "Type the employee ID of the person who added this item.";
    if (isLegacyItem(item)) {
      hint += " This item was added before employee IDs were introduced, so you may leave it empty.";
    }
    const hintEl = el("p", "panel-note", hint);
    hintEl.id = hintId;
    field.appendChild(hintEl);
    form.appendChild(field);

    const error = el("p", "form-error", panel.error);
    error.id = errorId;
    error.setAttribute("role", "alert");
    form.appendChild(error);

    const actions = el("div", "delete-actions");
    const confirm = el("button", "btn btn-small btn-danger", panel.busy ? "Deleting..." : "Confirm delete");
    confirm.type = "submit";
    confirm.disabled = panel.busy;
    const cancel = el("button", "btn btn-small btn-secondary", "Cancel");
    cancel.type = "button";
    cancel.dataset.action = "cancel-delete";
    cancel.dataset.itemId = item.id;
    cancel.disabled = panel.busy;
    actions.append(confirm, cancel);
    form.appendChild(actions);
    return form;
  }

  function renderItemCard(item, me) {
    const taken = isTaken(item);
    const li = el("li", "item-card");
    li.dataset.itemId = item.id;

    li.appendChild(renderMedia(item));

    const body = el("div", "item-body");
    body.appendChild(el("p", "item-title", item.title));
    const meta = renderMeta(item);
    if (meta) body.appendChild(meta);
    body.appendChild(
      el("p", "item-bringing", `Bringing: ${taken ? String(item.claimed_by).trim() : "Not assigned"}`)
    );
    body.appendChild(
      el("span", `badge ${taken ? "taken" : "available"}`, taken ? "Already Taken" : "Available")
    );

    const panelOpen = !taken && deletePanel !== null && deletePanel.itemId === item.id;
    const actions = el("div", "item-actions");
    if (!taken) {
      actions.appendChild(actionButton(item, "claim"));
      if (!panelOpen) actions.appendChild(actionButton(item, "delete"));
    } else if (me && normName(item.claimed_by) === me) {
      actions.appendChild(actionButton(item, "unclaim"));
    }
    if (actions.childElementCount > 0) body.appendChild(actions);
    if (panelOpen) body.appendChild(renderDeletePanel(item, deletePanel));

    li.appendChild(body);
    return li;
  }

  /** Find the control matching a remembered focus target (exact, then same item). */
  function findFocusTarget(target) {
    const controls = Array.from(itemsList.querySelectorAll("[data-action]"));
    return (
      controls.find((b) => b.dataset.itemId === target.itemId && b.dataset.action === target.action) ||
      controls.find((b) => b.dataset.itemId === target.itemId && !b.disabled) ||
      null
    );
  }

  function renderItems() {
    const active = document.activeElement;
    let focusTarget = null;
    if (restoreFocus) {
      focusTarget = restoreFocus;
    } else if (active && itemsList.contains(active) && active.dataset && active.dataset.itemId) {
      focusTarget = { itemId: active.dataset.itemId, action: active.dataset.action };
      if (typeof active.selectionStart === "number") {
        focusTarget.selection = [active.selectionStart, active.selectionEnd];
      }
    }
    restoreFocus = null;

    const me = normName(nameInput.value);
    itemsList.replaceChildren(...items.map((item) => renderItemCard(item, me)));
    itemsEmpty.hidden = items.length > 0;

    if (focusTarget) {
      const next = findFocusTarget(focusTarget);
      if (next) {
        next.focus();
        if (focusTarget.selection && next.dataset.action === "delete-input") {
          try {
            next.setSelectionRange(focusTarget.selection[0], focusTarget.selection[1]);
          } catch {
            /* ignore */
          }
        }
      }
    }
  }

  function renderSummary() {
    const total = items.length;
    if (total === 0) {
      itemsSummary.textContent = "";
      return;
    }
    const available = items.filter((i) => !isTaken(i)).length;
    itemsSummary.textContent = `${total} ${total === 1 ? "item" : "items"} - ${available} available`;
  }

  function renderPeople() {
    const people = derivePeople(items);
    const unassigned = items.filter((i) => !isTaken(i)).length;

    if (items.length === 0) {
      peopleMeta.textContent = "";
    } else if (unassigned > 0) {
      peopleMeta.textContent = `${unassigned} ${unassigned === 1 ? "item still needs" : "items still need"} someone`;
    } else {
      peopleMeta.textContent = "Every item has someone bringing it. Thank you!";
    }

    peopleEmpty.hidden = people.length > 0;
    peopleList.replaceChildren(
      ...people.map((person) => {
        const li = el("li", "person-card");
        li.appendChild(el("p", "person-name", person.name));
        const ul = el("ul", "person-items");
        for (const item of person.items) ul.appendChild(el("li", null, item.title));
        li.appendChild(ul);
        return li;
      })
    );
  }

  function renderAll() {
    renderItems();
    renderSummary();
    renderPeople();
  }

  function panelHasFocus() {
    const active = document.activeElement;
    return Boolean(active && itemsList.contains(active) && active.closest(".delete-confirm"));
  }

  /**
   * Drop the delete panel if its item vanished or was claimed.
   * @returns {boolean} true when focus should move to the items summary.
   */
  function dropStalePanel() {
    if (!deletePanel) return false;
    const item = items.find((i) => i.id === deletePanel.itemId);
    const hadFocus = panelHasFocus();
    if (!item) {
      // A busy panel means our own delete is in flight: the removal is expected.
      const expected = deletePanel.busy;
      deletePanel = null;
      if (!expected) showToast("That item was removed, so the delete form was closed.");
      return hadFocus;
    }
    if (isTaken(item) && !deletePanel.busy) {
      deletePanel = null;
      showToast("That item was just taken, so it can no longer be deleted.");
      if (hadFocus) restoreFocus = { itemId: item.id, action: "unclaim" };
    }
    return false;
  }

  function onState(nextItems) {
    items = Array.isArray(nextItems) ? nextItems : [];
    const focusSummary = dropStalePanel();
    renderAll();
    if (focusSummary) itemsSummary.focus();
  }

  /* ---- Claim / unclaim --------------------------------------------------- */

  async function runItemAction(btn, item, action, call, successMessage) {
    const key = `${item.id}:${action}`;
    if (pending.has(key)) return;
    pending.add(key);
    btn.disabled = true;
    setItemsError("");
    let ok = false;
    try {
      await call();
      ok = true;
    } catch (err) {
      setItemsError(errorMessage(err));
    } finally {
      pending.delete(key);
      restoreFocus = { itemId: item.id, action };
      renderItems();
    }
    if (ok) showToast(successMessage);
  }

  function handleClaim(btn, item) {
    const name = nameInput.value.trim();
    if (!name) {
      nameError.textContent = "Please enter your name first";
      nameInput.focus();
      return;
    }
    nameError.textContent = "";
    runItemAction(btn, item, "claim", () => api.claimItem(item.id, name), "Thanks! You are bringing it.");
  }

  function handleUnclaim(btn, item) {
    const name = nameInput.value.trim();
    if (!name) {
      nameError.textContent = "Please enter your name first";
      nameInput.focus();
      return;
    }
    runItemAction(btn, item, "unclaim", () => api.unclaimItem(item.id, name), "Item is available again.");
  }

  /* ---- Delete with inline confirmation ---------------------------------- */

  function openDeletePanel(item) {
    if (deletePanel && deletePanel.busy) return; // one request at a time
    clearTimeout(deleteDoneTimer);
    deletePanel = { itemId: item.id, value: "", error: "", busy: false };
    restoreFocus = { itemId: item.id, action: "delete-input" };
    renderItems();
  }

  function closeDeletePanel() {
    if (!deletePanel || deletePanel.busy) return;
    const itemId = deletePanel.itemId;
    deletePanel = null;
    restoreFocus = { itemId, action: "delete" };
    renderItems();
  }

  async function submitDelete(form) {
    const panel = deletePanel;
    if (!panel || panel.busy || panel.itemId !== form.dataset.itemId) return;
    const item = items.find((i) => i.id === panel.itemId);
    if (!item || isTaken(item)) return;

    const input = form.querySelector("input[data-action='delete-input']");
    if (input) panel.value = input.value;
    const employeeId = panel.value.trim();

    if (!employeeId && !isLegacyItem(item)) {
      panel.error = "Please enter the employee ID.";
      restoreFocus = { itemId: panel.itemId, action: "delete-input" };
      renderItems();
      return;
    }

    panel.error = "";
    panel.busy = true;
    // Keep focus on the input (not the soon-disabled Confirm button).
    restoreFocus = { itemId: panel.itemId, action: "delete-input" };
    renderItems();

    const itemId = panel.itemId;
    try {
      await api.deleteItem(itemId, employeeId);
    } catch (err) {
      const message = deleteErrorMessage(err);
      if (deletePanel && deletePanel.itemId === itemId) {
        deletePanel.busy = false;
        deletePanel.error = message;
        restoreFocus = { itemId, action: "delete-input" };
        renderItems();
      } else {
        showToast(message);
      }
      return;
    }

    showToast("Item deleted");
    if (deletePanel && deletePanel.itemId === itemId) {
      // The ItemDeleted event hasn't removed the card yet. The panel stays busy
      // (so no second request can go out) until the event or a reconnect
      // snapshot arrives; if that takes too long, hand control back.
      deleteDoneTimer = setTimeout(() => {
        if (deletePanel && deletePanel.itemId === itemId) {
          deletePanel = null;
          restoreFocus = { itemId, action: "delete" };
          renderItems();
        }
      }, 5000);
    }
  }

  itemsList.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const btn = target.closest("button[data-action]");
    if (!btn || !itemsList.contains(btn) || btn.disabled) return;
    const item = items.find((i) => i.id === btn.dataset.itemId);
    if (!item) return;
    switch (btn.dataset.action) {
      case "claim":
        handleClaim(btn, item);
        break;
      case "unclaim":
        handleUnclaim(btn, item);
        break;
      case "delete":
        openDeletePanel(item);
        break;
      case "cancel-delete":
        closeDeletePanel();
        break;
      default:
        break;
    }
  });

  itemsList.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof Element) || !form.matches("form.delete-confirm")) return;
    event.preventDefault();
    submitDelete(form);
  });

  itemsList.addEventListener("input", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.dataset.action !== "delete-input") return;
    if (!deletePanel || deletePanel.itemId !== input.dataset.itemId || deletePanel.busy) return;
    deletePanel.value = input.value;
    if (deletePanel.error) {
      deletePanel.error = "";
      const errorEl = input.closest("form") && input.closest("form").querySelector(".form-error");
      if (errorEl) errorEl.textContent = "";
    }
  });

  itemsList.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !deletePanel) return;
    const target = event.target;
    if (!(target instanceof Element) || !target.closest(".delete-confirm")) return;
    event.preventDefault();
    closeDeletePanel();
  });

  /* ---- Name -------------------------------------------------------------- */

  nameInput.value = readStoredName();
  // The add-form name starts from the saved claim name and follows it only
  // until the user edits the add-form name themselves.
  let addNameDirty = false;
  addNameInput.value = nameInput.value;
  nameInput.addEventListener("input", () => {
    storeName(nameInput.value.trim());
    nameError.textContent = "";
    if (!addNameDirty) addNameInput.value = nameInput.value;
    renderItems();
  });
  nameForm.addEventListener("submit", (event) => event.preventDefault());

  /* ---- Add item ---------------------------------------------------------- */

  const addFields = [addNameInput, employeeInput, titleInput, dateInput, imageInput];

  function fieldErrorEl(input) {
    return $(`${input.id}-error`);
  }

  function setFieldError(input, message) {
    fieldErrorEl(input).textContent = message || "";
    if (message) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  }

  function clearAddErrors() {
    for (const input of addFields) setFieldError(input, "");
    addError.textContent = "";
  }

  /** Client-side checks mirroring the server. Returns the first invalid input or null. */
  function validateAddForm(values) {
    const problems = new Map();
    if (!values.added_by) problems.set(addNameInput, "Please enter your name");
    else if (charLength(values.added_by) > LIMITS.name) {
      problems.set(addNameInput, `Name must be ${LIMITS.name} characters or fewer`);
    }
    if (!values.employee_id) problems.set(employeeInput, "Please enter your employee ID");
    else if (charLength(values.employee_id) > LIMITS.employeeId) {
      problems.set(employeeInput, `Employee ID must be ${LIMITS.employeeId} characters or fewer`);
    }
    if (!values.title) problems.set(titleInput, "Please enter a food item");
    else if (charLength(values.title) > LIMITS.title) {
      problems.set(titleInput, `Food item must be ${LIMITS.title} characters or fewer`);
    }
    if (!values.bring_date) problems.set(dateInput, "Please choose a date");
    else if (!isValidIsoDate(values.bring_date)) {
      problems.set(dateInput, "Enter a valid date as YYYY-MM-DD");
    }
    if (values.image_url && !safeImageUrl(values.image_url)) {
      problems.set(imageInput, "Image link must start with http:// or https://");
    }

    let first = null;
    for (const input of addFields) {
      const message = problems.get(input) || "";
      setFieldError(input, message);
      if (message && !first) first = input;
    }
    return first;
  }

  addNameInput.addEventListener("input", () => {
    addNameDirty = true;
  });

  addForm.addEventListener("input", (event) => {
    const input = event.target;
    if (addFields.includes(input)) setFieldError(input, "");
    addError.textContent = "";
  });

  for (const btn of document.querySelectorAll("button[data-suggest]")) {
    btn.addEventListener("click", () => {
      titleInput.value = btn.dataset.suggest || "";
      setFieldError(titleInput, "");
      addError.textContent = "";
      titleInput.focus();
    });
  }

  addForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (addSubmit.disabled) return;

    const values = {
      added_by: addNameInput.value.trim(),
      employee_id: employeeInput.value.trim(),
      title: titleInput.value.trim(),
      bring_date: dateInput.value.trim(),
      image_url: imageInput.value.trim(),
    };

    addError.textContent = "";
    const firstInvalid = validateAddForm(values);
    if (firstInvalid) {
      firstInvalid.focus();
      return;
    }

    addSubmit.disabled = true;
    try {
      await api.addItem({ ...values, image_url: values.image_url || null });
      // Clear only title, image and employee id; keep the name and the date.
      titleInput.value = "";
      imageInput.value = "";
      employeeInput.value = "";
      clearAddErrors();
      showToast("Item added");
      titleInput.focus();
    } catch (err) {
      // Keep every entered value and add no card.
      addError.textContent = errorMessage(err);
    } finally {
      addSubmit.disabled = false;
    }
  });

  /* ---- Tabs -------------------------------------------------------------- */

  function activateTab(name, { focus = false, updateHash = true } = {}) {
    if (!TABS.includes(name)) return;
    for (const tabName of TABS) {
      const selected = tabName === name;
      const tab = $(`tab-${tabName}`);
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      tab.tabIndex = selected ? 0 : -1;
      $(`panel-${tabName}`).hidden = !selected;
      if (selected && focus) tab.focus();
    }
    if (updateHash && window.location.hash !== `#${name}`) {
      try {
        window.history.replaceState(null, "", `#${name}`);
      } catch {
        /* ignore */
      }
    }
  }

  function tabFromHash() {
    const name = window.location.hash.replace(/^#/, "");
    return TABS.includes(name) ? name : null;
  }

  tabButtons.forEach((tab, index) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      let nextIndex = null;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % TABS.length;
      else if (event.key === "ArrowLeft") nextIndex = (index - 1 + TABS.length) % TABS.length;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = TABS.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      activateTab(TABS[nextIndex], { focus: true });
    });
  });

  window.addEventListener("hashchange", () => {
    const name = tabFromHash();
    if (name) activateTab(name, { updateHash: false });
  });

  /* ---- Feature tiles ----------------------------------------------------- */

  for (const tile of document.querySelectorAll("[data-goto]")) {
    tile.addEventListener("click", (event) => {
      event.preventDefault();
      switch (tile.dataset.goto) {
        case "add":
          activateTab("items");
          scrollToEl(addForm);
          titleInput.focus({ preventScroll: true });
          break;
        case "name":
          scrollToEl(nameForm);
          nameInput.focus({ preventScroll: true });
          break;
        case "items":
          activateTab("items", { focus: true });
          scrollToEl($("panel-items"));
          break;
        case "share":
          activateTab("share");
          scrollToEl($("panel-share"));
          copyBtn.focus({ preventScroll: true });
          break;
        default:
          break;
      }
    });
  }

  /* ---- Share ------------------------------------------------------------- */

  const boardUrl = window.location.origin + window.location.pathname;
  shareInput.value = boardUrl;
  shareInput.addEventListener("focus", () => shareInput.select());

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch {
        /* fall through to the legacy path */
      }
    }
    try {
      shareInput.focus();
      shareInput.select();
      shareInput.setSelectionRange(0, text.length);
      return document.execCommand("copy");
    } catch {
      return false;
    }
  }

  copyBtn.addEventListener("click", async () => {
    shareError.textContent = "";
    const ok = await copyText(boardUrl);
    if (ok) {
      showToast("Link copied");
    } else {
      shareError.textContent = "Couldn't copy automatically. The link is selected, so press Ctrl+C (or long-press) to copy it.";
      shareInput.focus();
      shareInput.select();
    }
  });

  if (typeof navigator.share === "function") {
    webShareBtn.hidden = false;
    webShareBtn.addEventListener("click", async () => {
      shareError.textContent = "";
      try {
        await navigator.share({
          title: "Potluck Week 2026 | PIRNAV",
          text: "Pick what you'll bring to the PIRNAV Potluck Week 2026!",
          url: boardUrl,
        });
      } catch (err) {
        if (!err || err.name !== "AbortError") {
          shareError.textContent = "Couldn't open the share sheet. Use Copy link instead.";
        }
      }
    });
  }

  /* ---- Start ------------------------------------------------------------- */

  const initialTab = tabFromHash();
  if (initialTab) activateTab(initialTab, { updateHash: false });
  renderAll();

  const client = new BoardClient({
    boardId: id,
    api,
    onState,
    onStatus,
    onError: () => {
      /* The connection badge already shows "Reconnecting..."; retries are automatic. */
    },
  });
  client.start();
}
