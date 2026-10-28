const CACHE_NAME = "kebab-bonus-v5-private-pages-never-cached";

self.addEventListener("install", () => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(Promise.all([
        caches.keys().then(names => Promise.all(names.filter(name => name.startsWith('kebab-bonus-')).map(name => caches.delete(name)))),
        self.clients.claim()
    ]));
});

self.addEventListener("fetch", (event) => {
    if (event.request.method !== 'GET') return;
    // Never display an old customer/staff page after logout or offline.
    event.respondWith(fetch(event.request).catch(() => new Response(
        'Keine Verbindung. Bitte Internetverbindung prüfen und erneut laden.',
        {status:503,headers:{'Content-Type':'text/plain; charset=utf-8','Cache-Control':'no-store'}}
    )));
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

    let zielUrl = new URL(
        zielPfad,
        self.location.origin
    ).href;
    if (new URL(zielUrl).origin !== self.location.origin) zielUrl = self.location.origin + '/';

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

