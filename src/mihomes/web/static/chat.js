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

  scrollToBottom();

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
      case "resync":
        location.reload();
        break;
      case "revoked":
        source.close();
        location.href = "/";
        break;
    }
  }

  var source = new EventSource("/chat/stream?after=" + encodeURIComponent(root.dataset.lastEvent || "0"));
  ["post.created", "post.edited", "post.deleted", "reaction.changed", "read.changed",
   "conversation.created", "typing", "resync", "revoked"].forEach(function (type) {
    source.addEventListener(type, function (e) {
      var data;
      try { data = JSON.parse(e.data); } catch (_) { return; }
      handle(type, data);
    });
  });
  window.addEventListener("pagehide", function () { source.close(); });

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") markRead(newestSeq);
  });

  // Typing: at most one POST per 3 s (the server throttles too).
  var lastTypingSent = 0;
  body.addEventListener("input", function () {
    var now = Date.now();
    if (now - lastTypingSent < 3000 || !body.value.trim()) return;
    lastTypingSent = now;
    htmx.ajax("POST", "/chat/c/" + conversationId + "/typing", { swap: "none" });
  });

  window.MiHomesChat = {
    conversationId: conversationId,
    list: list,
    nearBottom: nearBottom,
    scrollToBottom: scrollToBottom,
  };
})();
