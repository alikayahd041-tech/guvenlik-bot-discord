 Discord + Roblox Rütbelendirme Botu

Türkçe slash komutlarıyla çalışan başlangıç projesi.

## Komutlar
- `/duyuru baslik mesaj etiket`
- `/otomatik-cevap tetikleyici cevap`
- `/tam-yasak-at kisi sebep kanit`
- `/kick kisi sebep kanit`
- `/ses-gir kanal`
- `/ses-cik`
- `/muzik url` — doğrudan HTTP(S) ses akışı URL'si ve FFmpeg gerektirir; YouTube arama/çalma değildir.
- `/rütbe-degistir roblox_isim rutbe sebep`
- `/rütbeler`

## Kurulum
1. Python 3.11 veya daha yenisini yükle.
2. `pip install -r requirements.txt`
3. `.env.example` dosyasını `.env` olarak kopyala ve değerleri doldur.
4. Discord Developer Portal > Bot bölümünde **Message Content Intent** ve **Server Members Intent** aç.
5. Botu sunucuya `bot` ve `applications.commands` kapsamlarıyla ekle. Gerekli Discord izinlerini ver: mesaj gönderme, embed, üyeleri yasaklama/atma, ses bağlantısı ve konuşma.
6. `python bot.py` ile başlat.

## Güvenlik
- API anahtarını, bot tokenini veya `.env` dosyasını GitHub'a yükleme.
- GitHub deposunda `.env` yerine yalnızca `.env.example` bulunmalı.
- API anahtarında sadece gereken grup izinlerini etkinleştir; Roblox hesabının hedef toplulukta rol atama yetkisi olmalı.
- `/rütbe-degistir` komutu Discord'da `Rolleri Yönet` veya `Yönetici` izni ister. Bunu ayrıca istersen belirli bir Discord rol ID'siyle sınırla.
- `LOG_CHANNEL_ID` alanına log kanalının ID'sini yaz. Botun bu kanala mesaj gönderme izni olmalı.

## Roblox API notu
Kod, Roblox Open Cloud Groups API'sini kullanır. `ROBLOX_GROUP_ID` sayısal topluluk ID'si olmalıdır. Rol adı, Roblox API'nin döndürdüğü adla aynı yazılmalıdır. Open Cloud uç noktaları Beta olabilir ve Roblox tarafındaki yetkilere/erişime bağlı olarak hata döndürebilir. İlk testte düşük riskli bir kullanıcıyla ve doğru toplulukta deneyin.

## Müzik notu
`/ses-gir` ve `/ses-cik` ses kanalına katılma/ayrılma işlemleridir. `/muzik` doğrudan erişilebilir ses akışı URL'si ister; FFmpeg kurulu olmalıdır. Bir web sayfası veya video sayfası URL'si doğrudan ses akışı olmayabilir.
