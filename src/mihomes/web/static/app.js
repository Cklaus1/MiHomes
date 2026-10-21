/* MiHomes shared client behaviour — loaded once from <head>.
 *
 * **Why a static file and not an inline <script> in base.html.** About seventy forms swap
 * `hx-target="body"`, and htmx re-executes every <script> inside the swapped body. A listener
 * registered inline is therefore added again on every save: after five saves one failure would
 * show five toasts. <head> is never swapped, so everything here runs exactly once per page load.
 *
 * Four jobs:
 *   1. Failures are never silent. htmx 2 does not swap a 4xx/5xx, and the server answers an
 *      HTMX request's errors with JSON (errors.py `_wants_json`), so before this file a failed
 *      save, an expired session or a plan refusal left the button looking like it did nothing.
 *   2. Successes can say so: a route sets `HX-Trigger: {"toast": "Saved"}` and it appears here.
 *   3. Pending state: the submit button is disabled while its request is in flight, so a slow
 *      save cannot be clicked twice into a duplicate row.
 *   4. One modal behaviour for the ~45 modals: dialog semantics, Escape, focus in and back out,
 *      backdrop click. Previously ten pages each carried their own copy of openModal().
 */
(function () {
  'use strict';
  if (window.__mihomes) return;          // defensive: never install twice
  window.__mihomes = true;

  /* ------------------------------------------------------------------ toasts */

  function toastRegion() {
    let r = document.getElementById('toast-region');
    if (!r) {
      r = document.createElement('div');
      r.id = 'toast-region';
      r.className = 'mh-toast-region';
      r.setAttribute('role', 'status');
      r.setAttribute('aria-live', 'polite');
      // On <html>, not <body>: htmx fires HX-Trigger before it swaps, and ~70 forms swap the
      // whole body — a region inside it would be deleted along with the toast just added.
      document.documentElement.appendChild(r);
    }
    return r;
  }

  /** Show a toast. `kind` is 'success' | 'error' | 'info'. `link` is {href, text} or null. */
  function toast(message, kind, link) {
    if (!message) return;
    const el = document.createElement('div');
    el.className = 'mh-toast mh-toast-' + (kind || 'info');
    if (kind === 'error') el.setAttribute('role', 'alert');
    const text = document.createElement('span');
    text.textContent = message;
    el.appendChild(text);
    if (link && link.href) {
      const a = document.createElement('a');
      a.href = link.href;
      a.textContent = link.text || 'Open';
      a.className = 'mh-toast-link';
      el.appendChild(a);
    }
    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'mh-toast-close';
    x.setAttribute('aria-label', 'Dismiss');
    x.textContent = '×';
    x.addEventListener('click', () => el.remove());
    el.appendChild(x);
    toastRegion().appendChild(el);
    // Errors stay until dismissed or replaced; a success can fade.
    if (kind !== 'error') setTimeout(() => el.remove(), 4000);
  }
  window.mhToast = toast;

  /** Pull a human sentence out of whatever the server sent back. */
  function messageFrom(xhr) {
    const status = xhr.status;
    const type = (xhr.getResponseHeader('Content-Type') || '').toLowerCase();
    const body = xhr.responseText || '';
    let data = null;
    if (type.includes('json')) {
      try { data = JSON.parse(body); } catch (e) { data = null; }
    }
    if (status === 402) {
      return {
        text: (data && data.reason) || 'That needs a higher plan.',
        link: { href: '/billing', text: 'See plans' },
      };
    }
    if (status === 401) return { text: 'Your session has ended. Sign in again to continue.',
                                 link: { href: '/login', text: 'Sign in' } };
    if (status === 403) return { text: "You don't have permission to do that." };
    if (status === 404) return { text: 'That item no longer exists. Refresh to see the latest.' };
    if (data) {
      const d = data.detail || data.message || data.error_message;
      if (typeof d === 'string' && d && status < 500) return { text: d };
      if (Array.isArray(d) && d.length && d[0].msg) {      // FastAPI validation (422)
        const field = (d[0].loc || []).slice(-1)[0];
        const label = field ? String(field).replace(/_/g, ' ') : '';
        return { text: (label ? label.charAt(0).toUpperCase() + label.slice(1) + ': ' : '') + d[0].msg };
      }
      if (data.request_id) return { text: 'Something went wrong (ref ' + data.request_id + '). Nothing was saved.' };
    }
    // A short plain-text or small HTML body from a route is meant for the user.
    if (status < 500 && body && body.length < 400) {
      const tmp = document.createElement('div');
      tmp.innerHTML = body;
      const t = (tmp.textContent || '').trim();
      if (t) return { text: t };
    }
    return { text: status >= 500 ? 'Something went wrong on our side. Nothing was saved — please try again.'
                                 : 'That didn\'t work. Please check the form and try again.' };
  }

  /* ---------------------------------------------------------- htmx: errors */

  document.addEventListener('htmx:beforeSwap', function (e) {
    const xhr = e.detail.xhr;
    if (xhr.status < 400) return;
    // A route that re-renders the whole page with a `form_error`/`error` banner (status 400/422)
    // is answering with the fix, not just the fault — let it swap when it targets the body.
    const type = (xhr.getResponseHeader('Content-Type') || '').toLowerCase();
    const isPage = type.includes('html') && /<body[\s>]/i.test(xhr.responseText || '');
    if (isPage && e.detail.target === document.body && xhr.status < 500) {
      e.detail.shouldSwap = true;
      e.detail.isError = false;
    }
  });

  document.addEventListener('htmx:responseError', function (e) {
    const xhr = e.detail.xhr;
    // Polling fragments (alert badge, trial banner, weather) fail quietly: the slot stays empty.
    const elt = e.detail.elt;
    const trig = (elt && elt.getAttribute && elt.getAttribute('hx-trigger')) || '';
    if (/\bevery\b|^load$/.test(trig.trim())) return;
    if (xhr.getResponseHeader('HX-Redirect')) return;   // htmx is already navigating
    const m = messageFrom(xhr);
    toast(m.text, 'error', m.link);
  });

  document.addEventListener('htmx:sendError', function () {
    toast("Couldn't reach MiHomes. Check your connection and try again.", 'error');
  });

  /* A route announces success with `HX-Trigger: {"toast": "Saved"}` (or {"toast": {"message",
     "kind"}}). htmx dispatches it as an event on the requesting element; it bubbles here. */
  document.addEventListener('toast', function (e) {
    const v = e.detail && (e.detail.value !== undefined ? e.detail.value : e.detail);
    if (typeof v === 'string') toast(v, 'success');
    else if (v && v.message) toast(v.message, v.kind || 'success');
  });

  /* ------------------------------------------------------- htmx: pending */

  const PENDING = 'data-mh-pending';
  function submitters(elt) {
    if (!elt) return [];
    if (elt.tagName === 'FORM') {
      return Array.from(elt.querySelectorAll('button:not([type=button]), input[type=submit]'))
        .concat(Array.from(document.querySelectorAll('[form="' + elt.id + '"]')).filter(() => elt.id));
    }
    if (elt.tagName === 'BUTTON') return [elt];
    return [];
  }

  document.addEventListener('htmx:beforeRequest', function (e) {
    const cfg = e.detail.requestConfig || {};
    if ((cfg.verb || 'get').toLowerCase() === 'get') return;  // searches/polls never disable
    submitters(e.detail.elt).forEach((b) => {
      if (b.disabled) return;
      b.disabled = true;
      b.setAttribute(PENDING, '1');
      b.setAttribute('aria-busy', 'true');
    });
    document.documentElement.classList.add('mh-busy');
  });

  document.addEventListener('htmx:afterRequest', function (e) {
    submitters(e.detail.elt).forEach((b) => {
      if (!b.hasAttribute(PENDING)) return;
      b.disabled = false;
      b.removeAttribute(PENDING);
      b.removeAttribute('aria-busy');
    });
    document.documentElement.classList.remove('mh-busy');
  });

  /* --------------------------------------------------------------- modals */

  /* A modal root is a `fixed inset-0` element whose id names a modal ("add-task-modal",
     "task-modal-<id>", "upgrade-modal"). Matching the existing markup means every one of the
     ~45 modals gets this behaviour without a template sweep. Children such as
     "ordered-modal-title" are never `fixed inset-0`, so they do not match. */
  const MODAL_SEL = '.fixed.inset-0[id*="modal"]';

  function isOpen(m) { return !m.classList.contains('hidden'); }
  function openModals() { return Array.from(document.querySelectorAll(MODAL_SEL)).filter(isOpen); }

  function decorate(m) {
    if (m.hasAttribute('role')) return;
    m.setAttribute('role', 'dialog');
    m.setAttribute('aria-modal', 'true');
    const h = m.querySelector('h2, h3');
    if (h) {
      if (!h.id) h.id = m.id + '-heading';
      m.setAttribute('aria-labelledby', h.id);
    }
    // Icon-only close buttons were announced as an unnamed "button".
    m.querySelectorAll('button').forEach((b) => {
      if (b.getAttribute('aria-label') || (b.textContent || '').trim()) return;
      const oc = b.getAttribute('onclick') || '';
      if (/hidden|close/i.test(oc)) b.setAttribute('aria-label', 'Close');
      else if (b.title) b.setAttribute('aria-label', b.title);
    });
  }

  const returnFocus = new WeakMap();

  /* A form field if the dialog opens on one, else the Close button, else the dialog itself —
     never "the first button", which in a record modal is Delete. Only visible fields count:
     an edit form hidden behind a read-only view would otherwise take focus invisibly. */
  function focusFirst(m) {
    const visible = (x) => x && x.offsetParent !== null;
    const field = Array.from(m.querySelectorAll(
      '[autofocus], input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled])'
    )).find(visible);
    const close = Array.from(m.querySelectorAll('button[aria-label="Close"]')).find(visible);
    let f = field || close;
    if (!f) {
      f = m.querySelector('[role="dialog"] > *') || m;
      if (!f.hasAttribute('tabindex')) f.setAttribute('tabindex', '-1');
    }
    try { f.focus({ preventScroll: true }); } catch (err) { f.focus(); }
  }

  window.openModal = function (id) {
    const m = document.getElementById(id);
    if (!m) return;
    decorate(m);
    returnFocus.set(m, document.activeElement);
    m.classList.remove('hidden');
    focusFirst(m);
  };

  window.closeModal = function (id) {
    const m = typeof id === 'string' ? document.getElementById(id) : id;
    if (!m) return;
    m.classList.add('hidden');
    const back = returnFocus.get(m);
    if (back && document.contains(back)) back.focus();
  };

  /* Tabs inside a record modal: buttons `[data-tab-btn="<modal>:<tab>"]` or the legacy
     `switchTab(modalId, tab)` call with panels `#<modalId>-tab-<tab>`. */
  window.switchTab = function (modalId, tab) {
    const m = document.getElementById(modalId);
    if (!m) return;
    m.querySelectorAll('[id^="' + modalId + '-tab-"]').forEach((p) => p.classList.add('hidden'));
    const panel = document.getElementById(modalId + '-tab-' + tab);
    if (panel) panel.classList.remove('hidden');
    m.querySelectorAll('[data-tab]').forEach((b) => {
      const on = b.getAttribute('data-tab') === tab;
      b.classList.toggle('border-brand-600', on);
      b.classList.toggle('text-brand-600', on);
      b.classList.toggle('border-transparent', !on);
      b.classList.toggle('text-gray-500', !on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  };

  /* Modals opened by inline `classList.remove('hidden')` (the older pattern) still get
     semantics and focus: watch for the class flip. */
  const mo = new MutationObserver((records) => {
    records.forEach((r) => {
      const m = r.target;
      if (!(m instanceof Element) || !m.matches(MODAL_SEL)) return;
      const wasHidden = (r.oldValue || '').split(/\s+/).includes('hidden');
      if (wasHidden && isOpen(m)) {
        decorate(m);
        if (!m.contains(document.activeElement)) {
          if (!returnFocus.has(m)) returnFocus.set(m, document.activeElement);
          focusFirst(m);
        }
      }
    });
  });
  function watch() {
    mo.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['class'], attributeOldValue: true });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    const open = openModals();
    if (!open.length) return;
    // Topmost = last in document order with the highest z-index; DOM order is a good proxy.
    window.closeModal(open[open.length - 1]);
    e.stopImmediatePropagation();   // an open modal takes Escape before the drawer/search do
  });

  /* Backdrop click closes, consistently (some modals had it, others did not). A click that
     started inside the panel and ended on the backdrop (text selection) does not count. */
  let downOn = null;
  document.addEventListener('mousedown', (e) => { downOn = e.target; });
  document.addEventListener('click', function (e) {
    const t = e.target;
    if (!(t instanceof Element) || !t.matches(MODAL_SEL) || downOn !== t) return;
    if (t.hasAttribute('data-modal-static')) return;
    window.closeModal(t);
  });

  /* ------------------------------------------------- clickable rows/cards */

  /* Rows and cards that open a record via onclick were unreachable by keyboard. Give them a
     tab stop and Enter/Space. Applied after every swap. */
  /* Table rows keep their row role — overriding it to "button" strips the table structure a
     screen reader navigates by; the row's first cell carries the name anyway. Cards (div/li)
     have no such semantics to lose, so they become buttons. */
  const CLICKABLE = 'tr[onclick], tr[data-modal], div[onclick], li[onclick]';
  function keyboardify(root) {
    (root || document).querySelectorAll(CLICKABLE).forEach((el) => {
      if (el.hasAttribute('tabindex')) return;
      el.setAttribute('tabindex', '0');
      el.setAttribute('data-mh-kbd', '');
      if (el.tagName !== 'TR' && !el.hasAttribute('role')) el.setAttribute('role', 'button');
    });
  }
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const el = e.target;
    if (!(el instanceof Element) || !el.hasAttribute('data-mh-kbd')) return;
    e.preventDefault();
    el.click();
  });

  /* ----------------------------------------------------------- platform */

  function platformKeys(root) {
    const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
    (root || document).querySelectorAll('[data-kbd-mod]').forEach((k) => {
      k.textContent = (mac ? '⌘' : 'Ctrl ') + k.getAttribute('data-kbd-mod');
    });
  }

  /* ------------------------------------------------------ mobile drawer */

  /* SPEC-009 G1 · A1/A4, moved here from base.html. Delegated, because a body swap replaces
     the toggle, sidebar and backdrop elements; listeners bound to the old ones would die.
     `aria-expanded` tracks state; Escape closes; focus moves into the drawer on open and back
     to the toggle on close. While closed below md the sidebar is `inert`: off-screen links
     were still in the tab order, so keyboard users tabbed through eighteen invisible items. */
  const drawer = { lastFocus: null };
  const el = (id) => document.getElementById(id);
  function drawerOpen() { const t = el('nav-toggle'); return !!t && t.getAttribute('aria-expanded') === 'true'; }
  function syncInert() {
    const s = el('sidebar');
    if (!s) return;
    s.inert = window.innerWidth < 768 && !drawerOpen();
  }
  function openDrawer() {
    const t = el('nav-toggle'), s = el('sidebar'), b = el('nav-backdrop');
    if (!t || !s || !b) return;
    drawer.lastFocus = document.activeElement;
    s.classList.remove('-translate-x-full');
    s.classList.add('translate-x-0');
    b.classList.remove('hidden');
    t.setAttribute('aria-expanded', 'true');
    syncInert();
    const first = s.querySelector('a, button');
    if (first) first.focus();
  }
  function closeDrawer() {
    const t = el('nav-toggle'), s = el('sidebar'), b = el('nav-backdrop');
    if (!t || !s || !b) return;
    s.classList.add('-translate-x-full');
    s.classList.remove('translate-x-0');
    b.classList.add('hidden');
    t.setAttribute('aria-expanded', 'false');
    syncInert();
    if (drawer.lastFocus && document.contains(drawer.lastFocus)) drawer.lastFocus.focus();
  }
  document.addEventListener('click', function (e) {
    const t = e.target instanceof Element ? e.target : null;
    if (!t) return;
    if (t.closest('#nav-toggle')) { drawerOpen() ? closeDrawer() : openDrawer(); return; }
    if (t.closest('#nav-backdrop')) { closeDrawer(); return; }
    // Navigating closes it, or it stays open over the page the user just asked for.
    if (t.closest('#sidebar a') && drawerOpen()) closeDrawer();
  });
  window.addEventListener('resize', function () {
    if (window.innerWidth >= 768 && drawerOpen()) closeDrawer();
    syncInert();
  });

  /* ------------------------------------------------------- global search */

  function searchInput() { return el('global-search-input'); }
  function openSearch() {
    const w = el('global-search-wrap'), i = searchInput();
    if (!w || !i) return;
    w.classList.add('mh-search-open');     // below sm: shows the field as an overlay
    i.focus();
    i.select();
  }
  function closeSearch() {
    const w = el('global-search-wrap');
    if (w) w.classList.remove('mh-search-open');
    const r = el('global-search-results');
    if (r) r.classList.add('hidden');
  }
  document.addEventListener('click', function (e) {
    const t = e.target instanceof Element ? e.target : null;
    if (t && t.closest('#global-search-open')) { openSearch(); return; }
    const wrap = el('global-search-wrap');
    if (wrap && t && !wrap.contains(t)) closeSearch();
  });
  document.addEventListener('focusin', function (e) {
    if (e.target && e.target.id === 'global-search-input') {
      const r = el('global-search-results');
      if (r) r.classList.remove('hidden');
    }
  });
  document.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      openSearch();
      return;
    }
    if (e.key === 'Escape') {
      if (drawerOpen()) { closeDrawer(); return; }
      const w = el('global-search-wrap');
      if (w && (w.classList.contains('mh-search-open') || w.contains(document.activeElement))) closeSearch();
    }
    // Arrow keys move through the search dropdown's results.
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && el('global-search-wrap') &&
        el('global-search-wrap').contains(document.activeElement)) {
      const links = Array.from(document.querySelectorAll('#global-search-results a'));
      if (!links.length) return;
      e.preventDefault();
      const i = links.indexOf(document.activeElement);
      const next = e.key === 'ArrowDown' ? (i + 1) % links.length : (i <= 0 ? links.length - 1 : i - 1);
      links[next].focus();
    }
  });

  /* ------------------------------------------------- notes count badges */

  /* After any notes-container swap, recount note items and update the nearest badge. */
  document.addEventListener('htmx:afterSettle', function (e) {
    const container = e.detail.elt;
    if (!container || !container.hasAttribute || !container.hasAttribute('data-notes-container')) return;
    const count = container.querySelectorAll('[data-note-item]').length;
    const badgeId = container.dataset.notesBadge;
    if (badgeId) {
      const badge = document.getElementById(badgeId);
      if (badge) badge.textContent = count;
    }
  });

  /* ---------------------------------------------- document preview overlay */

  function previewEsc(e) { if (e.key === 'Escape') window.closePreview(); }
  window.previewDoc = function (url, title, type) {
    el('preview-title').textContent = title;
    el('preview-open-link').href = url;
    const container = el('preview-content');
    container.textContent = '';
    if (type === 'image') {
      const img = document.createElement('img');
      img.src = url; img.alt = title;
      img.style.cssText = 'max-width:100%;max-height:75vh;object-fit:contain;border-radius:8px;';
      container.appendChild(img);
    } else if (type === 'pdf') {
      const f = document.createElement('iframe');
      f.src = url; f.title = title;
      f.style.cssText = 'width:100%;height:75vh;border:none;border-radius:4px;';
      container.appendChild(f);
    }
    el('preview-overlay').classList.remove('hidden');
    document.addEventListener('keydown', previewEsc);
  };
  window.closePreview = function () {
    el('preview-overlay').classList.add('hidden');
    el('preview-content').textContent = '';
    document.removeEventListener('keydown', previewEsc);
  };
  /* Delegated preview trigger: elements carry data-preview-fp/title/type so a hostile
     document title can never break out of an inline onclick JS string. */
  document.addEventListener('click', function (e) {
    const trigger = e.target instanceof Element && e.target.closest('[data-preview-fp]');
    if (!trigger) return;
    e.preventDefault();
    window.previewDoc(
      trigger.getAttribute('data-preview-fp'),
      trigger.getAttribute('data-preview-title') || '',
      trigger.getAttribute('data-preview-type') || 'image'
    );
  });

  function init(root) {
    keyboardify(root);
    platformKeys(root);
    syncInert();
  }

  document.addEventListener('DOMContentLoaded', function () { init(document); watch(); });
  document.addEventListener('htmx:afterSettle', function (e) {
    init(e.detail.elt && e.detail.elt.parentElement ? e.detail.elt.parentElement : document);
    // A body swap replaces <body>'s children; the observer is on <body> itself and survives.
  });
})();
