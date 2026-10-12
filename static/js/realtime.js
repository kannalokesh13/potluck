/**
 * Realtime board client: snapshot + WebSocket change events with reconnect.
 * Plain ES module, no dependencies.
 * @module realtime
 */

/**
 * Build the people list from items, matching the server's grouping:
 * key is trimmed lowercase claimed_by, first-seen casing is the display name,
 * items keep their order, unclaimed items are skipped.
 * @param {Array<{claimed_by: string|null}>} items
 * @returns {Array<{name: string, items: Array}>}
 */
export function derivePeople(items) {
  const groups = new Map();
  for (const item of items || []) {
    const claimer = item.claimed_by ? String(item.claimed_by).trim() : "";
    if (!claimer) continue;
    const key = claimer.toLowerCase();
    let person = groups.get(key);
    if (!person) {
      person = { name: claimer, items: [] };
      groups.set(key, person);
    }
    person.items.push(item);
  }
  return Array.from(groups.values());
}

/**
 * Apply a change event to an items list, returning a NEW array.
 * ItemAdded appends if the id is unseen; ItemClaimed/ItemUnclaimed replace the
 * item with the same id (appending if missing). Input is never mutated.
 * @param {Array<{id: string}>} items
 * @param {{type: string, item: {id: string}}} event
 * @returns {Array}
 */
export function applyEvent(items, event) {
  const next = items.slice();
  if (!event || !event.item) return next;
  const idx = next.findIndex((i) => i.id === event.item.id);
  switch (event.type) {
    case "ItemAdded":
      if (idx === -1) next.push(event.item);
      break;
    case "ItemClaimed":
    case "ItemUnclaimed":
      if (idx === -1) next.push(event.item);
      else next[idx] = event.item;
      break;
    default:
      break;
  }
  return next;
}

/** Sort by created_at ascending (stable; returns a new array). */
function sortItems(items) {
  return items
    .map((item, i) => ({ item, i }))
    .sort((a, b) => {
      // created_at is an epoch-seconds number from the server; compare numerically.
      const x = Number(a.item.created_at) || 0;
      const y = Number(b.item.created_at) || 0;
      return x < y ? -1 : x > y ? 1 : a.i - b.i;
    })
    .map((e) => e.item);
}

const BACKOFF_BASE_MS = 1000;
const BACKOFF_MAX_MS = 15000;
const PING_INTERVAL_MS = 25000;

/**
 * Keeps a live, convergent copy of a board's items.
 *
 * Sequence on start and every reconnect: fetch snapshot -> open socket ->
 * wait for "subscribed" ack -> fetch snapshot again (closes the race window),
 * buffering events that arrive meanwhile and applying them afterwards.
 */
export class BoardClient {
  /**
   * @param {Object} options
   * @param {string} options.boardId
   * @param {{getState: () => Promise<{items: Array}>}} options.api
   * @param {(items: Array) => void} [options.onState]
   * @param {(status: "connecting"|"online"|"offline") => void} [options.onStatus]
   * @param {(err: Error) => void} [options.onError]
   */
  constructor({ boardId, api, onState, onStatus, onError }) {
    this.boardId = boardId;
    this.api = api;
    this.onState = onState || (() => {});
    this.onStatus = onStatus || (() => {});
    this.onError = onError || (() => {});

    this.items = [];
    this._stopped = true;
    this._ws = null;
    this._attempt = 0;
    this._reconnectTimer = null;
    this._pingTimer = null;
    this._generation = 0; // invalidates in-flight work from older connections
    this._status = null;
    this._onVisibility = this._onVisibility.bind(this);
  }

  /** Begin syncing. Safe to call again after stop(). */
  start() {
    if (!this._stopped) return;
    this._stopped = false;
    this._attempt = 0;
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", this._onVisibility);
    }
    this._connect();
  }

  /** Stop syncing: cancel timers, close the socket, prevent reconnects. */
  stop() {
    this._stopped = true;
    this._generation++;
    clearTimeout(this._reconnectTimer);
    this._reconnectTimer = null;
    this._stopPing();
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", this._onVisibility);
    }
    this._closeSocket();
  }

  _setStatus(status) {
    if (this._status === status) return;
    this._status = status;
    this.onStatus(status);
  }

  _setItems(items) {
    this.items = sortItems(items);
    this.onState(this.items.slice());
  }

  async _fetchSnapshot() {
    const state = await this.api.getState();
    return Array.isArray(state && state.items) ? state.items : [];
  }

  async _connect() {
    if (this._stopped) return;
    const gen = ++this._generation;
    clearTimeout(this._reconnectTimer);
    this._reconnectTimer = null;
    this._setStatus("connecting");

    try {
      const items = await this._fetchSnapshot();
      if (gen !== this._generation) return;
      this._setItems(items);
    } catch (err) {
      if (gen !== this._generation) return;
      this.onError(err);
      this._scheduleReconnect();
      return;
    }
    this._openSocket(gen);
  }

  _wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}/board/${encodeURIComponent(this.boardId)}/ws`;
  }

  _openSocket(gen) {
    let ws;
    try {
      ws = new WebSocket(this._wsUrl());
    } catch (err) {
      this.onError(err);
      this._scheduleReconnect();
      return;
    }
    this._ws = ws;

    let subscribed = false;
    let buffer = [];

    ws.onmessage = (msg) => {
      if (gen !== this._generation) return;
      if (typeof msg.data !== "string" || msg.data === "pong") return;
      let data;
      try {
        data = JSON.parse(msg.data);
      } catch {
        return;
      }
      if (!data || typeof data !== "object") return;

      if (data.type === "subscribed") {
        if (subscribed) return;
        subscribed = true;
        this._startPing();
        this._resync(gen, ws, () => {
          const pending = buffer;
          buffer = null;
          return pending;
        });
        return;
      }

      if (!data.item) return;
      if (buffer) {
        buffer.push(data);
      } else {
        this._setItems(applyEvent(this.items, data));
      }
    };

    ws.onclose = () => {
      if (gen !== this._generation) return;
      this._handleDisconnect();
    };
    ws.onerror = () => {
      // A close event follows; just make sure we reconnect even if it doesn't.
      if (gen !== this._generation) return;
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    };
  }

  /** Second snapshot after the subscribe ack, then replay buffered events. */
  async _resync(gen, ws, takeBuffer) {
    try {
      const items = await this._fetchSnapshot();
      if (gen !== this._generation) return;
      let merged = items;
      for (const ev of takeBuffer()) merged = applyEvent(merged, ev);
      this._attempt = 0;
      this._setItems(merged);
      this._setStatus("online");
    } catch (err) {
      if (gen !== this._generation) return;
      this.onError(err);
      this._handleDisconnect();
    }
  }

  _handleDisconnect() {
    this._generation++; // ignore any further callbacks from this socket
    this._stopPing();
    this._closeSocket();
    this._setStatus("offline");
    this._scheduleReconnect();
  }

  _scheduleReconnect() {
    if (this._stopped) return;
    this._setStatus("offline");
    clearTimeout(this._reconnectTimer);
    const base = Math.min(BACKOFF_BASE_MS * 2 ** this._attempt, BACKOFF_MAX_MS);
    const delay = base + Math.random() * base * 0.2; // small jitter
    this._attempt++;
    this._reconnectTimer = setTimeout(() => this._connect(), delay);
  }

  _closeSocket() {
    const ws = this._ws;
    this._ws = null;
    if (!ws) return;
    ws.onmessage = ws.onclose = ws.onerror = null;
    try {
      ws.close();
    } catch {
      /* ignore */
    }
  }

  _startPing() {
    this._stopPing();
    this._pingTimer = setInterval(() => {
      const ws = this._ws;
      if (ws && ws.readyState === 1) {
        try {
          ws.send("ping");
        } catch {
          /* close handler will deal with it */
        }
      }
    }, PING_INTERVAL_MS);
  }

  _stopPing() {
    clearInterval(this._pingTimer);
    this._pingTimer = null;
  }

  _onVisibility() {
    if (this._stopped || document.visibilityState !== "visible") return;
    const ws = this._ws;
    const open = ws && (ws.readyState === 0 || ws.readyState === 1);
    if (!open && this._status !== "connecting") {
      this._attempt = 0;
      this._connect();
    }
  }
}
