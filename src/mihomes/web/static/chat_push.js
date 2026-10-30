// Push opt-in for this device (SPEC-014). Permission is requested only from a click.
// Swaps no HTML; uses no Tailwind classes beyond toggling `hidden`.
(function () {
  "use strict";
  var box = document.getElementById("chat-push-device");
  if (!box || box.dataset.wired) return;
  box.dataset.wired = "1";
  var key = box.dataset.vapidKey;
  var status = box.querySelector("[data-chat-push-status]");
  var enable = box.querySelector("[data-chat-push-enable]");
  var disable = box.querySelector("[data-chat-push-disable]");
  if (!key || !status) return;

  function say(text) { status.textContent = text; }
  function jsonHeaders() { return { "Content-Type": "application/json" }; }

  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    say("This browser can't receive push notifications here.");
    return;
  }

  function b64ToBytes(b64) {
    var pad = "=".repeat((4 - (b64.length % 4)) % 4);
    var raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function registration() {
    return navigator.serviceWorker.register("/sw.js", { scope: "/" })
      .then(function () { return navigator.serviceWorker.ready; });
  }

  function refresh() {
    registration().then(function (reg) { return reg.pushManager.getSubscription(); })
      .then(function (sub) {
        if (Notification.permission === "denied") {
          say("Notifications are blocked for this site in your browser settings.");
          enable.hidden = true; disable.hidden = true; return;
        }
        say(sub ? "Push is on for this device." : "Push is off for this device.");
        enable.hidden = !!sub; disable.hidden = !sub;
      })
      .catch(function () { say("Couldn't check push on this device."); });
  }

  enable.addEventListener("click", function () {
    Notification.requestPermission().then(function (perm) {
      if (perm !== "granted") { refresh(); return; }
      return registration()
        .then(function (reg) {
          return reg.pushManager.subscribe({ userVisibleOnly: true,
                                             applicationServerKey: b64ToBytes(key) });
        })
        .then(function (sub) {
          return fetch("/chat/push/subscribe", { method: "POST", credentials: "same-origin",
                                                  headers: jsonHeaders(),
                                                  body: JSON.stringify(sub.toJSON()) });
        })
        .then(function (r) {
          if (!r.ok) return r.text().then(function (t) { say(t || "Couldn't turn push on."); });
          refresh();
        });
    }).catch(function () { say("Couldn't turn push on."); });
  });

  disable.addEventListener("click", function () {
    registration().then(function (reg) { return reg.pushManager.getSubscription(); })
      .then(function (sub) {
        if (!sub) return;
        var endpoint = sub.endpoint;
        return sub.unsubscribe().then(function () {
          return fetch("/chat/push/unsubscribe", { method: "POST", credentials: "same-origin",
                                                    headers: jsonHeaders(),
                                                    body: JSON.stringify({ endpoint: endpoint }) });
        });
      })
      .then(refresh)
      .catch(function () { say("Couldn't turn push off."); });
  });

  refresh();
})();
