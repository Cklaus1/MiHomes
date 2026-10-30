// MiHomes service worker (SPEC-014 D12, N4). Exactly two events: push and notificationclick.
// NO fetch handler and NO Cache Storage: caching authenticated pages on the device would keep
// tenant data there after sign-out or revocation, readable offline by whoever holds the phone.
"use strict";

self.addEventListener("push", function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { data = {}; }
  // Chrome and Safari require a visible notification for every push (userVisibleOnly), so one
  // is always shown — with fixed fallback text if the payload was unreadable.
  var title = typeof data.title === "string" ? data.title : "MiHomes";
  event.waitUntil(self.registration.showNotification(title, {
    body: typeof data.body === "string" ? data.body : "New message",
    tag: typeof data.tag === "string" ? data.tag : "mihomes-chat",
    icon: "/static/icons/icon-192.png",
    data: { url: typeof data.url === "string" ? data.url : "/chat/" },
  }));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var target;
  try {
    target = new URL((event.notification.data && event.notification.data.url) || "/chat/",
                     self.location.origin);
  } catch (_) {
    target = new URL("/chat/", self.location.origin);
  }
  if (target.origin !== self.location.origin) target = new URL("/chat/", self.location.origin);
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true })
    .then(function (clients) {
      for (var i = 0; i < clients.length; i++) {
        var c = clients[i];
        if (new URL(c.url).origin === self.location.origin && "focus" in c) {
          return c.focus().then(function (fc) { return fc.navigate ? fc.navigate(target.href) : fc; });
        }
      }
      return self.clients.openWindow(target.href);
    }));
});
