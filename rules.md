# AI MAFIA — `rules.md` (Single Source of Truth)
Version: **1.0**  
Date: **2026-02-20**  
Owners: **Product Owner / Engineering Lead / UI-UX Lead**  
Status: **LOCKED** — Bu dosya güncellenmeden **hiçbir mimari, ekonomi, güvenlik veya monetizasyon kararı değiştirilemez.**

> Bu belge, “AI MAFIA” mobil oyununun **ürün anayasası + teknik blueprint + geliştirme yol haritasıdır**.  
> Oyun içi suç/şiddet temaları **tamamen kurgusaldır**; bu doküman gerçek hayatta yasa dışı eylemlere yönelik talimat değildir.

---

## 0) Değişiklik Politikası
- Bu dosya **Single Source of Truth**’tur.
- Her değişiklik:
  1) PR açılır  
  2) PO + Lead Eng + Lead UX onayı alınır  
  3) Versiyon **major/minor** artırılır (örn. 1.0 → 1.1)
- “Config ile yönetilecek” alanlar bu dokümanda işaretlenir; config değişiklikleri de changelog’a yazılır.

---

## 1) Ürün Vizyonu

### 1.1 Ton ve Atmosfer
- **1930’lar neo-noir**: ciddi, sert, düşük ışık, kirli şehir.
- **Kara mizah kırıntısı**: Sopranos / Guy Ritchie (Snatch) tadında kısa, sivri, absürt dokunuşlar.
- Metin dili: kısa cümle, görsel betimleme, argo (ölçülü), “cool” ama edebi değil.

### 1.2 Core Loop
Mobil kısa oturum (30–120 sn):
1. **Nerve/Energy** harca  
2. **İş/operasyon** seç (PvE, market, aile görevi, vb.)  
3. **Risk** (Heat, yaralanma/hastane, kayıp)  
4. **Ödül** (Cash/XP/itibar/loot/intel)  
5. **Progression** (Rank unlock, mülk, aile gücü)

### 1.3 Tasarım Sütunları
**Non‑negotiables**
- **P2W yok.** Premium para (💎) güç (stat/mermi/HP vb.) satın alamaz.
- Risk/ödül matematiği **şeffaf** ve **kural tabanlı**dır.
- Sosyal meta: **Aile (Guild) = yapıştırıcı**.
- Live‑ops hazır: event/season sistemi config ile yönetilir.
- AI sadece **anlatı/flavor** üretir; oyun matematiğine **dokunmaz**.

---

## 2) Teknik Temeller (Tech Stack & Mimari)

### 2.1 İstemci (Client)
- **Flutter** (iOS-first), neo‑noir dark theme.
- UI: kısa aksiyonlar, okunabilir tipografi, “tek ekranda karar”.
- Network: offline tolerans (retry/backoff), idempotency header desteği.

### 2.2 Backend
- **FastAPI (Python 3.12)** — async-first.
- Katmanlar:
  - `api` (routing)  
  - `domain` (iş kuralları)  
  - `infra` (db, cache, queue, external)  
  - `workers` (cron/async jobs)

### 2.3 Veri Katmanı
- **PostgreSQL**
  - İlişkisel state (accounts, ranks, families, properties)
  - **Immutable Economy Ledger** (append-only)
- **pgvector**
  - AI “bodyguard memory” ve NPC bağlamı için RAG.
- **Redis**
  - Cooldown/energy regen, rate limit, ephemeral chat fan‑out, SSE state.

### 2.4 AI Katmanı
- OpenAI (model seçimi config ile) + Function Calling / JSON schema output.
- AI yalnızca:
  - görev metni (flavor),
  - NPC diyalog tonu,
  - aile event hikâyeleştirme,
  - moderasyon sınıflandırma
  yapar.

> **AI ASLA**: ödül/ceza hesaplamaz, kazanma olasılığı belirlemez, ekonomi balansına karar vermez.

### 2.5 Altyapı (Infra)
- AWS: ECS Fargate (api + worker), RDS (Postgres), ElastiCache (Redis).
- Observability: structured logging + metrics + alerting (Sentry/CloudWatch vs).
- Secret management: AWS Secrets Manager / Parameter Store.

---

## 3) Ekonomi ve Ledger — Mutlak Kurallar

### 3.1 Para Birimleri (V1)
- `CASH` ($): günlük akış
- `DIAMOND` (💎): premium (IAP)
- `BULLET` (🔫 mermi): **ultimate sink** (Milestone 3)
- (Opsiyonel gelecek) `CLEAN` ($$): büyük yatırım (konseptte var; V1 kapsamı **config**)

### 3.2 Ledger Invariants (Kırmızı Çizgiler)
1) Ledger **append-only**: geçmiş kayıt silinmez/değişmez.  
2) Her ekonomik işlem **idempotent** olmalı:
   - `idempotency_key` unique (scope: user + action)
3) Para hareketi **RESERVE → CAPTURE → RELEASE** modeliyle yürür.
4) `wallet_balance` asla “elle” set edilmez; sadece ledger akışından türetilir.
5) Günlük reconciliation:
   - `SUM(wallets) == SUM(ledger captures - ledger releases)` doğrulanır.
   - Sapma = **SEV-1 incident**.

### 3.3 Ledger İşlem Tipleri (Minimum)
- `EARN` (görev ödülü)
- `SPEND` (upgrade, bakım, rüşvet/avukat/hastane, vb.)
- `TAX` (aile vergisi %10)
- `TRANSFER` (aile kasası ↔ yetkili dağıtımı)
- `RESERVE` / `CAPTURE` / `RELEASE` (hold mekanizması)

### 3.4 “BULLET” Ultimate Sink Kuralı
- Mermi **ledger’da currency**’dir.
- Saldırıda harcanan mermi `SPEND(BULLET)` olarak **kalıcı** gider.
- Mermi üretimi kontrollüdür (economy config).

---

## 4) Progression — Rank, XP, Nerve, Heat

### 4.1 Rank Tablosu (LOCKED)
| Rank | XP | Unlocks | Daily Nerve Cap |
|---|---:|---|---:|
| 1. Empty‑Suit | 0 | Solo küçük suçlar (Mugging) | 50 |
| 2. Runner | 1,000 | Araba çalma, Karaborsa/Market | 75 |
| 3. Enforcer | 5,000 | PvP başlar, NPC koruma kiralama | 100 |
| 4. Capo | 25,000 | Aile kur/katıl, Safehouse satın alma | 150 |
| 5. Fixer | 100,000 | Speakeasy/Depo, para aklama (opsiyonel) | 200 |
| 6. Underboss | 500,000 | Territory liderliği, suikast ihalesi | 250 |
| 7. Godfather | 2,000,000 | Casino, global leaderboard | 300 |

> Not: Rank matematiği ve unlock’lar bu tabloda kilitlidir. Değişiklik = rules.md versiyon bump.

### 4.2 Nerve/Energy Sistemi
- `Nerve` Redis üzerinden takip edilir.
- Yenilenme: **3 dakikada +1** (config: `ENERGY_TICK_SECONDS=180`).
- Sistem “last_updated + computed refill” ile deterministik olmalı (race yok).
- Aksiyonlar Nerve harcar, harcama ledger’a **yazılmaz** (ledger sadece ekonomi).

### 4.3 Heat (Polis İlgisi)
- Heat 0–100 ölçeği.
- Heat, gelir cezası ve risk modifiye eder (config):
  - örn: Heat > 80 ⇒ mülk geliri -%30
- Heat düşürme: avukat/rüşvet/hastane gibi **sink**’lerle.

---

## 5) PvE Suçlar (Milestone 1 scope)
- Buton tabanlı aksiyonlar:
  - Yankesicilik
  - Araba Çalma
  - (Config ile genişletilebilir)
- RNG:
  - Başarı olasılıkları **kural tabanlı** (rank + gear + skill).
- Sonuçlar:
  - `EARN(CASH)` ve XP artışı
  - Heat artışı
  - Fail durumunda kayıp/süre uzaması (config)

---

## 6) Mülk, Kiralama, Koruma — Ekonomi Motoru (Milestone 2 scope)

### 6.1 Mülk Tipleri (V1)
- Safehouse
- Speakeasy
- Casino (endgame)

### 6.2 Gelir Formülü (LOCKED)
**Günlük Net Gelir**
```
net = (base_income * security_multiplier) - (maintenance_cost + heat_penalty)
```
- `base_income`: mülk tipine göre
- `security_multiplier`: koruma ile artar (örn. 0.8–1.2)
- `maintenance_cost`: sabit sink
- `heat_penalty`: Heat eşiklerine bağlı ceza

Gelir dağıtımı:
- Günlük cron/worker çalışır → `EARN(CASH)` ledger kaydı → wallet.

### 6.3 Koruma (NPC Guards)
- Koruma kiralama: düzenli `SPEND(CASH)`
- Koruma seviyesi mülk riskini düşürür, geliri stabilize eder.

---

## 7) Aile (Syndicate) & Social (Milestone 2 scope)

### 7.1 RBAC Roller (LOCKED)
- Don
- Underboss
- Consigliere
- Capo
- Soldier/Associate

RBAC kuralları:
- Yetki matrisi DB seviyesinde enforce edilir.
- Tüm kritik aksiyonlar audit log üretir (kasa çekimi, terfi, savaş ilanı).

### 7.2 Aile Kasası (Vault)
- Üye kazancından **%10 vergi** otomatik kesilir:
  - oyuncu `EARN(CASH)` → `TAX(CASH)` → aile wallet `EARN(CASH)`
- Dağıtım/çekim sadece yetkililer (RBAC) ile.

### 7.3 Chat & Unified Inbox
- SSE ile gerçek zamanlı aile chat + global chat.
- Moderasyon zorunlu (bkz. Safety).
- Rate limit + flood koruması.

---

## 8) PvP Combat (Milestone 3 scope) — Asenkron, Anti‑Grief

### 8.1 Asenkron Çatışma Kuralları
- Saldırı “mermi harcar” (BULLET sink).
- Simülasyon backend’de deterministik hesaplanır (seed + logs).
- Sonuç: rapor + hospital/loot/heat.

### 8.2 Basit Denge Modeli (V1)
**Başarı Olasılığı**
```
powerAtt = basePower + gear + skillBonus
powerDef = baseDefense + guard + skillBonus
p = powerAtt / (powerAtt + powerDef)
p clamped to [0.20, 0.80]
```

**Ganimet**
```
loot = min(targetLootPool * 0.25, lootCapByRank)
```

### 8.3 Hastane / Koma
- 0 HP ⇒ **48 saat kilit** (config)
- XP kaybı **%15** (config)
- Bu mekanik retention etkisi ölçülür; gerekirse config ile ayarlanır.

### 8.4 Anti‑Grief Kalkanları
- Newbie Shield: ilk 7 gün veya Rank < 4 hedef olamaz / ganimet çok düşük.
- Aynı hedefe tekrar saldırı: azalan getiri + artan risk.
- Saldırı sonrası kısa “shield” (cooldown).

---

## 9) AI Özellikleri (Milestone 3–4 scope)

### 9.1 AI Flavor Engine (Görev Kaplaması)
- Backend görev parametrelerini belirler:
  - risk, lokasyon, npc, seçenek etiketleri
- AI’dan istenen çıktı:
  - max 50–80 kelime olay metni
  - 2–3 seçenek
  - **rakam yok** (ödül/ceza yok)
  - JSON schema’ya uyum

### 9.2 AI Bodyguards (RAG + Sadakat)
- Korumalar “hafıza” için pgvector kullanır:
  - önceki konuşmaların özetleri
  - oyuncu ile ilişki state’i
- “Sadakat puanı” **kural tabanlı** hesaplanır; AI sadece diyalog üretir.

### 9.3 Moderasyon (Zorunlu)
- UGC (chat, aile isimleri, profil metni) otomatik filtrelenir.
- Report/abuse kuyruklanır ve admin panel üzerinden işlenir.

### 9.4 Kill Switch / Degrade Mode (LOCKED)
- AI servisleri kapatılınca:
  - statik metin template’leri devreye girer
  - oyun **devam eder**
- AI maliyet limiti aşıldığında otomatik degrade.

---

## 10) Live Ops & Monetization & Store Release (Milestone 4 scope)

### 10.1 Monetizasyon (LOCKED)
- IAP: **consumable 💎 paketleri**
- VIP Abonelik (auto-renewable):
  - QoL bonusları (örn. daha hızlı cooldown, kozmetik)
- **Kesin yasak:** Stat/HP/Mermi satışları.

### 10.2 Safety Layer (App Store uyumu için kritik)
- UGC moderasyon, report sistemi, bloklama.
- Veri gizliliği: PII minimizasyonu, saklama politikası.

### 10.3 App Review / Test Erişimi (Uygun ve Şeffaf Yöntem)
> Not: App Store review sürecini **aldatmaya yönelik** “gizli bypass/sonsuz para” gibi yaklaşımlar bu projede **kullanılmaz**.

Uygun yöntem:
- Review Notes içinde **test kullanıcı hesabı** sağlanır.
- Sunucu tarafında **Demo Data Seeder** ile hesap için kontrollü içerik/para sağlanır (yalnızca demo hesap).
- IAP testleri için Apple’ın **sandbox** mekanizması kullanılır.
- “Review Mode” gerekiyorsa:
  - yalnızca **review hesabına** atanır,
  - açıkça dokümante edilir,
  - prod kullanıcıya açılmaz.

### 10.4 AI Destekli Global Eventler
- Haftalık sunucu event’i:
  - “Polis Baskını”, “Liman Grevi” vb.
- Mekanik etkiler config ile (drop rate, heat mod, income mod).
- AI yalnızca hikâyeyi yazar.

---

## 11) Governance & Operasyon

### 11.1 Unit Economics (Hedef)
- Ortalama AI maliyeti (cost per action) vs IAP geliri.
- Hedef brüt marj: **≥ %55**.

### 11.2 Ledger Tutarlılığı
- Her gün 04:00 reconciliation job:
  - mismatch ⇒ incident + otomatik “economy freeze” (opsiyonel)

### 11.3 Retention
- D1/D7/D30 takip.
- Koma yaşayan oyuncu churn analizi; gerekiyorsa config düzeltmesi.

### 11.4 Enflasyon Kontrolü
- Sunucu toplam CASH ve BULLET günlük büyüme takip.
- Aşırı büyümede:
  - bakım gideri artırılır (sink),
  - drop azaltılır (source),
  - event modları ayarlanır (config).

---

## 12) Milestone Yol Haritası (Sırayla)

### Milestone 1 — Core & Economy (Motorun İnşası)
**Hedef:** temel loop + ledger + rank. AI kapalı/mocked.

**Scope**
- Auth: Apple Sign‑In + Email OTP, 18+ gating
- Ledger: wallets + credit_ledger, reserve/capture/release, idempotency
- Nerve + cooldown (Redis)
- PvE suçlar (buton tabanlı)
- Rank/XP unlock sistemi

**DoD**
- Yeni kullanıcı: giriş → suç → ödül ledger → rank atlama uçtan uca çalışır.
- Double‑spend yok (idempotency testleri).

---

### Milestone 2 — Syndicate & Social (Sosyal Tutkal)
**Hedef:** aile + chat + mülk geliri.

**Scope**
- Aile RBAC (DB enforce)
- Aile kasası ve %10 vergi
- SSE chat + inbox
- Mülk satın alma + günlük pasif gelir cron

**DoD**
- Aile kuruluyor/katılınıyor, kasa büyüyor, chat stabil, mülk geliri dağıtılıyor.

---

### Milestone 3 — Combat & AI (Kan ve Ruh)
**Hedef:** asenkron PvP + mermi ekonomisi + AI flavor + AI bodyguards.

**Scope**
- PvP saldırı/savunma simülasyonu
- Koma/hastane, newbie shield, diminishing returns
- BULLET currency ledger entegrasyonu
- AI flavor engine (JSON output) + cache
- Bodyguard RAG (pgvector) + sadakat kuralları

**DoD**
- PvP raporları doğru, mermi sink çalışıyor, AI metinleri güvenli ve tutarlı.

---

### Milestone 4 — Live Ops, Monetization & Release
**Hedef:** Store release kalitesi, monetizasyon, safety, kill switch.

**Scope**
- StoreKit IAP + VIP subscription (QoL/kozmetik)
- Moderasyon + report queue + admin süreçleri
- Global events + season config
- Degrade mode / kill switch
- Release checklist + review test hesabı + demo seed

**DoD**
- Ödeme alınıyor, safety layer çalışıyor, AI kapansa bile oyun ayakta.

---

## 13) “Bugün Başlıyoruz” — İlk Engineering Checklist (Kickoff)
1) Repo yapısı:
   - `apps/mobile_flutter/`
   - `services/api_fastapi/`
   - `services/worker/`
   - `infra/`
   - `docs/` (bu dosya burada)
2) CI:
   - lint + test + migration check
3) DB migrations:
   - Alembic initial
4) Ledger MVP:
   - wallets, ledger tables + idempotency
5) Flutter skeleton:
   - auth flow + home actions screen (stub)
6) Config sistemi:
   - feature flags + economy parameters
7) Observability:
   - request id, structured logs, error tracking

---

## 14) Ek: AI Prompt / Output Kuralları (Kısa)
- Max 80 kelime.
- Neo‑noir + kara mizah (az).
- Asla:
  - rakamsal ödül/ceza,
  - gerçek dünya yasa dışı talimat,
  - nefret/şiddet teşviki (UGC moderasyon).

---

**Bu dosya, projenin tek kaynağıdır.**
