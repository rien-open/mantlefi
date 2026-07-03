// MantleFi PWA service worker — installability shell ONLY.
// Deliberately NO caching: this is a live research tool; a stale cached number would violate
// the product's core guarantee (every figure is fetched fresh and verifiable on-chain).
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", function () { /* network passthrough — browser default fetch */ });
