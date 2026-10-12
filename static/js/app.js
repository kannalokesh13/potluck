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

/** Turn any thrown value into a friendly message. */
function errorMessage(err) {
  if (err instanceof ApiError) {
    if (err.status === 409 && err.currentClaimer) {
      return `Already taken by ${err.currentClaimer}`;
    }
    return err.message;
  }
  return "Something went wrong. Please try again.";
}

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
  const titleInput = $("item-title");
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
    } else {
      btn.classList.add("btn-danger-ghost");
      btn.textContent = "I can't bring it";
      btn.setAttribute("aria-label", `I can't bring ${item.title}`);
    }
    if (pending.has(`${item.id}:${action}`)) btn.disabled = true;
    return btn;
  }

  function renderItemCard(item, me) {
    const taken = isTaken(item);
    const li = el("li", "item-card");
    li.dataset.itemId = item.id;

    li.appendChild(renderMedia(item));

    const body = el("div", "item-body");
    body.appendChild(el("p", "item-title", item.title));
    body.appendChild(
      el("p", "item-bringing", `Bringing: ${taken ? String(item.claimed_by).trim() : "Not assigned"}`)
    );
    body.appendChild(
      el("span", `badge ${taken ? "taken" : "available"}`, taken ? "Already Taken" : "Available")
    );

    const actions = el("div", "item-actions");
    if (!taken) {
      actions.appendChild(actionButton(item, "claim"));
    } else if (me && normName(item.claimed_by) === me) {
      actions.appendChild(actionButton(item, "unclaim"));
    }
    if (actions.childElementCount > 0) body.appendChild(actions);

    li.appendChild(body);
    return li;
  }

  /** Find the button matching a remembered focus target (exact, then same item). */
  function findFocusTarget(target) {
    const buttons = Array.from(itemsList.querySelectorAll("button[data-action]"));
    return (
      buttons.find((b) => b.dataset.itemId === target.itemId && b.dataset.action === target.action) ||
      buttons.find((b) => b.dataset.itemId === target.itemId) ||
      null
    );
  }

  function renderItems() {
    const active = document.activeElement;
    let focusTarget = null;
    if (active && itemsList.contains(active) && active.dataset && active.dataset.itemId) {
      focusTarget = { itemId: active.dataset.itemId, action: active.dataset.action };
    } else if (restoreFocus) {
      focusTarget = restoreFocus;
    }
    restoreFocus = null;

    const me = normName(nameInput.value);
    itemsList.replaceChildren(...items.map((item) => renderItemCard(item, me)));
    itemsEmpty.hidden = items.length > 0;

    if (focusTarget) {
      const next = findFocusTarget(focusTarget);
      if (next) next.focus();
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

  function onState(nextItems) {
    items = Array.isArray(nextItems) ? nextItems : [];
    renderAll();
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

  itemsList.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const btn = target.closest("button[data-action]");
    if (!btn || !itemsList.contains(btn) || btn.disabled) return;
    const item = items.find((i) => i.id === btn.dataset.itemId);
    if (!item) return;
    if (btn.dataset.action === "claim") handleClaim(btn, item);
    else if (btn.dataset.action === "unclaim") handleUnclaim(btn, item);
  });

  /* ---- Name -------------------------------------------------------------- */

  nameInput.value = readStoredName();
  nameInput.addEventListener("input", () => {
    storeName(nameInput.value.trim());
    nameError.textContent = "";
    renderItems();
  });
  nameForm.addEventListener("submit", (event) => event.preventDefault());

  /* ---- Add item ---------------------------------------------------------- */

  for (const btn of document.querySelectorAll("button[data-suggest]")) {
    btn.addEventListener("click", () => {
      titleInput.value = btn.dataset.suggest || "";
      addError.textContent = "";
      titleInput.focus();
    });
  }

  addForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (addSubmit.disabled) return;

    const title = titleInput.value.trim();
    const image = imageInput.value.trim();

    if (!title) {
      addError.textContent = "Please enter a food item";
      titleInput.focus();
      return;
    }
    if (image && !safeImageUrl(image)) {
      addError.textContent = "Image link must start with http:// or https://";
      imageInput.focus();
      return;
    }

    addError.textContent = "";
    addSubmit.disabled = true;
    try {
      await api.addItem(title, image || null);
      addForm.reset();
      showToast("Item added");
      titleInput.focus();
    } catch (err) {
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
