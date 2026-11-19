// In-app chat (SPEC-012). Swaps server-rendered HTML only — no Tailwind classes live here (§0.7).
(function () {
  "use strict";

  var root = document.getElementById("chat-conversation");
  if (!root) return;
  var conversationId = root.dataset.conversation;
  var scroller = document.getElementById("chat-scroll");
  var list = document.getElementById("chat-posts");
  var composer = document.getElementById("chat-composer");

  function newToken() {
    return window.crypto && crypto.randomUUID ? crypto.randomUUID() : "";
  }

  function nearBottom() {
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
  }

  function scrollToBottom() {
    scroller.scrollTop = scroller.scrollHeight;
  }

  // ---- composer -----------------------------------------------------------
  composer.querySelector("[name=client_token]").value = newToken();
  var body = composer.querySelector("[name=body]");
  body.addEventListener("keydown", function (e) {
    // Enter sends; Shift+Enter is a new line.
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (body.value.trim() || fileInput.files.length) htmx.trigger(composer, "submit");
    }
  });

  // Attachments: show what was picked; the server validates type, size and count.
  var fileInput = composer.querySelector("[data-chat-files]");
  var fileNames = composer.querySelector("[data-chat-file-names]");
  fileInput.addEventListener("change", function () {
    var names = Array.prototype.map.call(fileInput.files, function (f) { return f.name; });
    fileNames.textContent = names.length ? "Attached: " + names.join(", ") : "";
    fileNames.hidden = !names.length;
  });
  composer.addEventListener("htmx:afterRequest", function (e) {
    if (e.detail.successful) { fileNames.textContent = ""; fileNames.hidden = true; }
  });
  composer.addEventListener("submit", function (e) {
    if (!body.value.trim() && !fileInput.files.length) e.preventDefault();
  });

  // ---- replies --------------------------------------------------------------
  var chip = document.getElementById("chat-reply-chip");
  function clearReply() {
    composer.querySelector("[name=reply_to_id]").value = "";
    chip.hidden = true;
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-chat-reply]");
    if (btn) {
      composer.querySelector("[name=reply_to_id]").value = btn.dataset.chatReply;
      chip.querySelector("[data-chat-reply-label]").textContent = btn.dataset.chatReplyName;
      chip.hidden = false;
      body.focus();
      return;
    }
    if (e.target.closest("[data-chat-reply-cancel]")) clearReply();
  });

  // ---- a send: the stream may already have inserted this post, so never append twice ----
  document.body.addEventListener("htmx:beforeSwap", function (e) {
    if (e.detail.target !== list || !e.detail.xhr) return;
    var tpl = document.createElement("template");
    tpl.innerHTML = (e.detail.serverResponse || "").trim();
    var node = tpl.content.firstElementChild;
    var existing = node && document.getElementById(node.id);
    if (existing) {
      e.detail.shouldSwap = false;
      existing.replaceWith(node);
      htmx.process(node);
    }
  });
  document.body.addEventListener("htmx:afterSwap", function (e) {
    if (e.detail.target === list) {
      var empty = document.getElementById("chat-empty");
      if (empty) empty.remove();
      scrollToBottom();
    }
  });

  // A search hit (?at=) scrolls to that post instead of the newest.
  var jump = list.querySelector("[data-chat-jump]");
  if (jump) { jump.scrollIntoView({ block: "center" }); jump.style.outline = "2px solid #93c5fd"; jump.style.borderRadius = "0.75rem"; }
  else scrollToBottom();

  // ---- realtime (SPEC-012 §5.5) ---------------------------------------------
  // Events carry ids only (D8). For a post we re-fetch its server-rendered partial, which re-runs
  // the visibility check; we never build post HTML here.
  var seen = new Set();          // event ids already handled (catch-up overlap, reconnects)
  var newestSeq = parseInt(root.dataset.newestSeq || "0", 10);
  var typingEl = document.getElementById("chat-typing");
  var typingTimer = null;
  var readTimer = null;

  function markRead(seq) {
    if (seq <= 0) return;
    clearTimeout(readTimer);
    readTimer = setTimeout(function () {
      htmx.ajax("POST", "/chat/c/" + conversationId + "/read",
                { values: { seq: seq }, swap: "none" });
    }, 400);
  }

  function upsertPost(postId) {
    fetch("/chat/posts/" + encodeURIComponent(postId), { credentials: "same-origin",
                                                         headers: { "HX-Request": "true" } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        if (!html) return;
        var tpl = document.createElement("template");
        tpl.innerHTML = html.trim();
        var node = tpl.content.firstElementChild;
        if (!node) return;
        var stick = nearBottom();
        var existing = document.getElementById(node.id);
        if (existing) {
          existing.replaceWith(node);          // our own send, an edit, a reaction, a delete
        } else {
          list.appendChild(node);
          var empty = document.getElementById("chat-empty");
          if (empty) empty.remove();
        }
        htmx.process(node);                    // wire its react/edit/delete buttons
        var seq = parseInt(node.dataset.seq || "0", 10);
        if (seq > newestSeq) {
          newestSeq = seq;
          if (document.visibilityState === "visible") markRead(seq);
        }
        if (stick) scrollToBottom();
      });
  }

  function bumpSidebar(convId) {
    var row = document.getElementById("chat-row-" + convId);
    if (!row) { location.reload(); return; }   // a conversation this page does not list yet
    var badge = row.querySelector("[data-chat-unread]");
    if (!badge) {
      badge = document.createElement("span");
      badge.setAttribute("data-chat-unread", "");
      badge.className = "inline-flex items-center justify-center min-w-[20px] h-5 px-1 text-xs font-bold bg-brand-600 text-white rounded-full";
      badge.textContent = "0";
      row.appendChild(badge);
    }
    var n = parseInt(badge.textContent, 10);
    badge.textContent = isNaN(n) ? "99+" : (n + 1 >= 100 ? "99+" : String(n + 1));
  }

  function showTyping(name) {
    typingEl.textContent = name + " is typing…";   // textContent: the name is user-controlled
    clearTimeout(typingTimer);
    typingTimer = setTimeout(function () { typingEl.textContent = ""; }, 4000);
  }

  // "MiHomes is thinking…" (SPEC-013 D17). Transient events are never replayed, so a reconnect
  // during a 70–110 s AI call could miss ai.done: each entry also expires on its own after 180 s.
  var thinkingEl = document.getElementById("chat-thinking");
  var thinking = {};
  function setThinking(postId, on) {
    if (!postId) return;
    clearTimeout(thinking[postId]);
    if (on) thinking[postId] = setTimeout(function () { setThinking(postId, false); }, 180000);
    else delete thinking[postId];
    if (thinkingEl) thinkingEl.hidden = Object.keys(thinking).length === 0;
  }

  function handle(type, data) {
    if (data.id != null) {
      if (seen.has(data.id)) return;
      seen.add(data.id);
    }
    var here = data.c === conversationId;
    switch (type) {
      case "post.created":
        if (here) upsertPost(data.p); else bumpSidebar(data.c);
        if (here) { typingEl.textContent = ""; }
        break;
      case "post.edited":
      case "post.deleted":
      case "reaction.changed":
        if (here && document.getElementById("post-" + data.p)) upsertPost(data.p);
        break;
      case "conversation.created":
        if (!document.getElementById("chat-row-" + data.c)) bumpSidebar(data.c);
        break;
      case "typing":
        if (here) showTyping(data.name || "Someone");
        break;
      case "ai.thinking":
        if (here) setThinking(data.p, true);
        break;
      case "ai.done":
        if (here) setThinking(data.p, false);
        break;
      case "resync":
        location.reload();
        break;
      case "revoked":
        closeStream();
        location.href = "/";
        break;
    }
  }

  // The stream. SPEC-014 D9: a tab hidden for 60 s closes its stream (browsers allow 6
  // connections per host, and the server caps 6 streams per member); it reopens from the last
  // event id it saw when the tab is visible again, so nothing is missed.
  var lastEventId = root.dataset.lastEvent || "0";
  var source = null;
  var hideTimer = null;
  var EVENT_TYPES = ["post.created", "post.edited", "post.deleted", "reaction.changed",
                     "read.changed", "conversation.created", "typing", "resync", "revoked",
                     "ai.thinking", "ai.done"];
  function openStream() {
    if (source) return;
    source = new EventSource("/chat/stream?after=" + encodeURIComponent(lastEventId));
    EVENT_TYPES.forEach(function (type) {
      source.addEventListener(type, function (e) {
        if (e.lastEventId) lastEventId = e.lastEventId;
        var data;
        try { data = JSON.parse(e.data); } catch (_) { return; }
        handle(type, data);
      });
    });
  }
  function closeStream() {
    if (source) { source.close(); source = null; }
  }
  openStream();
  window.addEventListener("pagehide", closeStream);

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") {
      clearTimeout(hideTimer);
      openStream();
      markRead(newestSeq);
    } else {
      hideTimer = setTimeout(closeStream, 60000);
    }
  });

  // Typing: at most one POST per 3 s (the server throttles too).
  var lastTypingSent = 0;
  body.addEventListener("input", function () {
    var now = Date.now();
    if (now - lastTypingSent < 3000 || !body.value.trim()) return;
    lastTypingSent = now;
    htmx.ajax("POST", "/chat/c/" + conversationId + "/typing", { swap: "none" });
  });

  // ---- @mentions -------------------------------------------------------------
  // Typing "@" + letters asks the server who in THIS chat matches; picking one inserts the plain
  // name and adds a hidden mention_ids hint. The server re-checks both (SPEC-013 D14).
  var picker = document.getElementById("chat-mention-picker");
  var pickerTimer = null;
  function mentionQuery() {
    var upto = body.value.slice(0, body.selectionStart);
    var m = /(?:^|\s)@([^\s@]{0,30})$/.exec(upto);
    return m ? m[1] : null;
  }
  function closePicker() { if (picker) { picker.hidden = true; picker.textContent = ""; } }
  body.addEventListener("input", function () {
    if (!picker) return;
    var q = mentionQuery();
    clearTimeout(pickerTimer);
    if (q === null) { closePicker(); return; }
    pickerTimer = setTimeout(function () {
      htmx.ajax("GET", "/chat/c/" + conversationId + "/mentionable?q=" + encodeURIComponent(q),
                { target: picker, swap: "innerHTML" }).then(function () { picker.hidden = false; });
    }, 150);
  });
  document.addEventListener("click", function (e) {
    var opt = e.target.closest("[data-chat-mention-id]");
    if (!opt) { if (picker && !picker.contains(e.target)) closePicker(); return; }
    var name = opt.dataset.chatMentionName;
    var caret = body.selectionStart;
    // A function replacement: a name like "$&" must not be read as a replacement pattern.
    var before = body.value.slice(0, caret).replace(/@([^\s@]{0,30})$/,
                                                    function () { return "@" + name + " "; });
    body.value = before + body.value.slice(caret);
    body.focus();
    body.setSelectionRange(before.length, before.length);
    var hint = document.createElement("input");
    hint.type = "hidden"; hint.name = "mention_ids"; hint.value = opt.dataset.chatMentionId;
    hint.setAttribute("data-chat-mention-hint", "");
    composer.appendChild(hint);
    closePicker();
  });
  composer.addEventListener("htmx:afterRequest", function (e) {
    if (!e.detail.successful) return;
    composer.querySelectorAll("[data-chat-mention-hint]").forEach(function (n) { n.remove(); });
  });

  window.MiHomesChat = {
    conversationId: conversationId,
    list: list,
    nearBottom: nearBottom,
    scrollToBottom: scrollToBottom,
  };
})();
