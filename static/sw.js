// Service worker: runs in the background on your phone so notifications arrive
// even when the dashboard is closed.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", event => {
    let d = {};
    try { d = event.data.json(); } catch (e) { d = { title: "Dashboard", body: event.data ? event.data.text() : "" }; }
    event.waitUntil(self.registration.showNotification(d.title || "Dashboard", {
        body: d.body || "",
        icon: "/static/icon-192.png",
        badge: "/static/icon-192.png",
        tag: d.tag || undefined,
        data: { url: d.url || "/alerts" },
    }));
});

// Tapping a notification opens the right page
self.addEventListener("notificationclick", event => {
    event.notification.close();
    const url = event.notification.data && event.notification.data.url || "/alerts";
    event.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
        for (const c of list) { if ("focus" in c) { c.navigate(url); return c.focus(); } }
        return clients.openWindow(url);
    }));
});
