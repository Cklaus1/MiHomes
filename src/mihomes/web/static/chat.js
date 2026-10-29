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
      if (body.value.trim()) htmx.trigger(composer, "submit");
    }
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

  // ---- after a send: drop the empty-state note, keep the newest in view ----
  document.body.addEventListener("htmx:afterSwap", function (e) {
    if (e.detail.target === list) {
      var empty = document.getElementById("chat-empty");
      if (empty) empty.remove();
      scrollToBottom();
    }
  });

  scrollToBottom();

  window.MiHomesChat = {
    conversationId: conversationId,
    list: list,
    nearBottom: nearBottom,
    scrollToBottom: scrollToBottom,
  };
})();
