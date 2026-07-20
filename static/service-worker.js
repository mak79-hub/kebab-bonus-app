const CACHE_NAME = "kebab-bonus-v3";

self.addEventListener("install", () => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});

self.addEventListener("push", (event) => {
    let daten = {
        title: "KEBAB HÖHLE",
        body: "Du hast eine neue Nachricht.",
        url: "/login"
    };

    if (event.data) {
        try {
            daten = {
                ...daten,
                ...event.data.json()
            };
        } catch (fehler) {
            daten.body = event.data.text();
        }
    }

    const optionen = {
        body: daten.body,
        icon: "/static/icons/icon-192.png",
        badge: "/static/icons/icon-192.png",
        data: {
            url: daten.url || "/login"
        },
        vibrate: [200, 100, 200],
        requireInteraction: false
    };

    event.waitUntil(
        self.registration.showNotification(
            daten.title || "KEBAB HÖHLE",
            optionen
        )
    );
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();

    const zielPfad =
        event.notification.data &&
        event.notification.data.url
            ? event.notification.data.url
            : "/";

    const zielUrl = new URL(
        zielPfad,
        self.location.origin
    ).href;

    event.waitUntil(
        clients.matchAll({
            type: "window",
            includeUncontrolled: true
        }).then(async (fensterListe) => {

            if (fensterListe.length > 0) {
                const fenster = fensterListe[0];

                if ("navigate" in fenster) {
                    const navigiertesFenster =
                        await fenster.navigate(zielUrl);

                    if (
                        navigiertesFenster &&
                        "focus" in navigiertesFenster
                    ) {
                        return navigiertesFenster.focus();
                    }
                }

                if ("focus" in fenster) {
                    return fenster.focus();
                }
            }

            if (clients.openWindow) {
                return clients.openWindow(zielUrl);
            }
        })
    );
});
