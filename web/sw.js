// XuanWu - service worker RONG, khong luu cache gi ca.
//
// Vi sao can file nay du khong dung bo nho dem: Chrome (dac biet tren
// Android) chi hien nut "Cai dat ung dung" khi trang co MOT service worker
// dang hoat dong voi bo xu ly "fetch" - day la dieu kien ky thuat, khong
// lien quan gi den viec co luu cache hay khong.
//
// File nay CHU Y khong cache bat cu thu gi: moi yeu cau deu chuyen thang toi
// mang (fetch(event.request)), giong het nhu khong co service worker. Lam
// vay de tranh dung rui ro giu nham ban HTML/JS cu sau khi web duoc sua -
// dung dieu ma ban da chon tranh o muc "co ban".
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  e.respondWith(fetch(e.request));
});
