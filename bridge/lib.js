/**
 * Pure, testable helpers for the WhatsApp bridge (spec M29).
 *
 * Kept free of Baileys/Express so they can be unit-tested with `node --test`.
 */

/**
 * Compact a JSONL message log down to its newest `max` valid lines.
 *
 * The bridge appends one line per message forever, but only ever serves the
 * last `MAX_MESSAGES` in memory — so the file grows without bound while the tail
 * is all that matters. On startup we rewrite the file to just the newest `max`
 * parseable records, dropping blank/corrupt lines. Returns the retained records.
 *
 * @param {string} content  raw file contents (may be empty)
 * @param {number} max      max records to keep
 * @returns {{records: object[], text: string}}
 */
function compactMessages(content, max) {
  const lines = (content || '').split('\n');
  const records = [];
  for (const line of lines) {
    const t = line.trim();
    if (!t) continue;
    try {
      records.push(JSON.parse(t));
    } catch (_) {
      // drop corrupt line
    }
  }
  const kept = max > 0 ? records.slice(-max) : records;
  const text = kept.length ? kept.map((r) => JSON.stringify(r)).join('\n') + '\n' : '';
  return { records: kept, text };
}

module.exports = { compactMessages };
