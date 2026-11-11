# Domen.uz — Tayyor Android APK, iOS WebApp va Server To'plami

Ushbu to'plamda **Domen.uz** loyihasining barcha qismlari to'liq tayyorlangan:

1. **`main.py`** — Yangilangan server:
   - Telegram Mini App va Telegram'siz mustaqil ilova rejimida ishlaydi.
   - `/api/sms/send` so'rovida 5 xonali tasdiqlash kodini to'g'ridan-to'g'ri sayt ekraniga (`code`) qaytaradi.
   - `/download` manzilida yuklab olish landing sahifasini, `/download/Domen.uz.apk` manzilida esa tayyor `.apk` faylni tarqatadi.
2. **`index.html`** — Asosiy ilova:
   - Telefon raqam kiritilishi bilan 5 xonali SMS kod ekranning yuqori qismida bildirishnoma banneri bo'lib tushadi va buyurtma oynasida doimiy saqlanib turadi.
   - Yuqori menyuda **"📲 Ilovani yuklab olish"** bo'limi mavjud.
3. **`download.html`** — Maxsus `/download` landing sahifasi (Android APK va iOS o'rnatish).
4. **`android-app/` va `.github/workflows/build-apk.yml`** — Rasmiy `Domen.uz.apk` mobil ilovasining to'liq manba kodi va avtomatik yig'uvchisi.

## Kompyuterga hech qanday og'ir dastur o'rnatmasdan `Domen.uz.apk` faylini 2 daqiqada olish:

1. GitHub'da yangi repozitoriy (masalan `domen-uz-app`) oching va ushbu arxiv ichidagi barcha fayllarni yuklang (`git push`).
2. GitHub repozitoriyangizning **Actions** bo'limiga kiring — u yerda **"Build Domen.uz Android APK"** avtomatik ishga tushadi.
3. 1–2 daqiqada jarayon yashil (`✅`) bo'lgach, pastdagi **Artifacts** qismidan tayyor **`Domen.uz.apk`** faylini yuklab oling.
4. Olingan `Domen.uz.apk` faylini `main.py` turgan papkaga tashlab qo'ying va `python main.py` ni ishga tushiring — shunda saytingizdagi **"Android uchun yuklab olish"** tugmasi foydalanuvchilarga to'g'ridan-to'g'ri `Domen.uz.apk` ni yuklab beradi!
