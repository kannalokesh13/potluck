/**
 * REST client helpers for the potluck board API.
 * Plain ES module, no dependencies.
 * @module api
 */

/**
 * @typedef {Object} Item
 * @property {string} id
 * @property {string} board_id
 * @property {string} title
 * @property {string|null} image_url
 * @property {string|null} claimed_by
 * @property {number} created_at Epoch seconds
 * @property {"Available"|"Already Taken"} status
 * @property {string|null} added_by
 * @property {string|null} employee_id
 * @property {string|null} bring_date "YYYY-MM-DD"
 */

const TIMEOUT_MESSAGE = "The request timed out. Please try again.";
const DELETE_TIMEOUT_MS = 10000;

/** Error thrown for any failed API call (HTTP error or network failure). */
export class ApiError extends Error {
  /**
   * @param {string} message User-friendly message.
   * @param {number} status HTTP status, or 0 for network failure.
   * @param {string|null} [detail] Raw detail from the server.
   * @param {string|null} [currentClaimer] Who holds the item on a 409.
   */
  constructor(message, status, detail = null, currentClaimer = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.currentClaimer = currentClaimer;
  }
}

/**
 * Extract the board id from a path like /board/<id>.
 * @param {{pathname: string}} [loc]
 * @returns {string|null}
 */
export function boardIdFromLocation(loc = window.location) {
  const match = /^\/board\/([^/]+)\/?$/.exec((loc && loc.pathname) || "");
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

/**
 * Turn the server's `detail` (string or validation array) into text.
 * @param {unknown} detail
 * @returns {string|null}
 */
function detailToMessage(detail) {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d.msg === "string" ? d.msg : null))
      .filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }
  return null;
}

/**
 * Create an API client bound to one board.
 * @param {string} boardId
 */
export function createApi(boardId) {
  const base = `/board/${encodeURIComponent(boardId)}`;

  /**
   * @param {string} path Path relative to the board.
   * @param {{method?: string, body?: unknown, timeoutMs?: number}} [opts]
   *   `timeoutMs` (optional) aborts the request after that many milliseconds.
   */
  async function request(path, { method = "GET", body, timeoutMs } = {}) {
    const controller = timeoutMs ? new AbortController() : null;
    let timedOut = false;
    const timer = controller
      ? setTimeout(() => {
          timedOut = true;
          controller.abort();
        }, timeoutMs)
      : null;

    try {
      let res;
      try {
        res = await fetch(base + path, {
          method,
          headers: {
            Accept: "application/json",
            ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          },
          body: body !== undefined ? JSON.stringify(body) : undefined,
          signal: controller ? controller.signal : undefined,
        });
      } catch {
        if (timedOut) throw new ApiError(TIMEOUT_MESSAGE, 0);
        throw new ApiError(
          "Can't reach the server. Check your connection and try again.",
          0
        );
      }

      let data = null;
      try {
        data = await res.json();
      } catch {
        data = null;
      }

      if (!res.ok) {
        const detail = data && data.detail !== undefined ? data.detail : null;
        const message =
          detailToMessage(detail) ||
          `Something went wrong (error ${res.status}). Please try again.`;
        const claimer = data && typeof data.current_claimer === "string" ? data.current_claimer : null;
        throw new ApiError(message, res.status, typeof detail === "string" ? detail : message, claimer);
      }
      return data;
    } finally {
      if (timer !== null) clearTimeout(timer);
    }
  }

  return {
    /** @returns {Promise<{board_id: string, items: Item[], people: Array}>} */
    getState: () => request("/state"),
    /** @returns {Promise<{board_id: string, people: Array}>} */
    getPeople: () => request("/people"),
    /**
     * @param {{title: string, added_by: string, employee_id: string,
     *          bring_date: string, image_url?: string|null}} payload
     * @returns {Promise<Item>}
     */
    addItem: ({ title, added_by, employee_id, bring_date, image_url }) =>
      request("/items", {
        method: "POST",
        body: { title, added_by, employee_id, bring_date, image_url: image_url || null },
      }),
    /**
     * Delete an Available item. The employee id travels in the JSON body only.
     * @param {string} itemId
     * @param {string} [employeeId]
     * @returns {Promise<Item>} The deleted item.
     */
    deleteItem: (itemId, employeeId) =>
      request(`/items/${encodeURIComponent(itemId)}/delete`, {
        method: "POST",
        body: { employee_id: employeeId ?? "" },
        timeoutMs: DELETE_TIMEOUT_MS,
      }),
    /** @returns {Promise<Item>} */
    claimItem: (itemId, name) =>
      request(`/items/${encodeURIComponent(itemId)}/claim`, {
        method: "POST",
        body: { name },
      }),
    /** @returns {Promise<Item>} */
    unclaimItem: (itemId, name) =>
      request(`/items/${encodeURIComponent(itemId)}/unclaim`, {
        method: "POST",
        body: { name },
      }),
  };
}
