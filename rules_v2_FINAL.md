
# AI MAFIA — rules.md
Version: 2.0 (FINAL LOCKED)
Date: 2026-02-20
Status: SINGLE SOURCE OF TRUTH (Milestone 1–4)

This document defines the complete product vision, technical architecture, economy invariants, progression rules, and milestone roadmap for AI MAFIA.  
No architectural, economy, monetization, or milestone decision may change without updating this file.

--------------------------------------------------------------------
0. CORE PRINCIPLES (NON‑NEGOTIABLES)
--------------------------------------------------------------------

1. No Pay‑to‑Win (P2W).
   - No stat, HP, damage, bullet, or combat advantage may be sold.
   - Premium currency (DIAMOND) is limited to QoL and cosmetic value.

2. Immutable Economy.
   - All financial actions pass through the Ledger Service.
   - No direct wallet UPDATE queries are allowed.

3. Integer Money Only.
   - All currencies stored as integer minor units.
   - No float/decimal for balances.

4. Idempotency Required.
   - Every state-changing request must include idempotency_key.
   - Scope: (owner_type, owner_id, action, idempotency_key).

5. AI Boundaries.
   - AI generates text only.
   - AI never calculates rewards, probabilities, or damage.

6. Append‑Only Ledger.
   - Ledger entries are never updated or deleted.
   - RESERVE → CAPTURE → RELEASE are separate rows.

--------------------------------------------------------------------
1. PRODUCT VISION
--------------------------------------------------------------------

Theme:
1930s neo-noir crime world. Serious tone with restrained dark humor.

Core Loop:
Spend Nerve → Perform Crime/Action → Risk (Heat/Damage) → Earn Reward → Progress Rank.

Session Length:
30–120 seconds.

Primary Meta:
Family (Guild) system drives long-term engagement.

--------------------------------------------------------------------
2. TECHNICAL ARCHITECTURE
--------------------------------------------------------------------

Client:
Flutter (iOS-first), dark theme UI.

Backend:
FastAPI (Python 3.12 async).

Database:
PostgreSQL.

Cache / Realtime:
Redis (Energy, cooldowns, rate limiting, SSE).

Vector Memory (M3+):
pgvector for NPC memory storage.

Infrastructure:
AWS (ECS Fargate, RDS, ElastiCache, S3).

--------------------------------------------------------------------
3. ECONOMY ARCHITECTURE
--------------------------------------------------------------------

Currencies:
- CASH (soft currency)
- DIAMOND (premium, IAP)
- BULLET (combat sink)

Database Tables:
- wallets
- ledger_entries (append-only)
- idempotency_keys

Ledger Invariants:
- Negative balances prohibited by DB constraint.
- Wallet balances are derived and reconciled against ledger.
- Daily reconciliation job required.
- Any mismatch = SEV-1 incident.

--------------------------------------------------------------------
4. PROGRESSION SYSTEM
--------------------------------------------------------------------

RANK TABLE (LOCKED)

Rank 1 – Empty-Suit (XP 0)
Unlock: Solo PvE crimes
Max Nerve: 50

Rank 2 – Runner (1,000 XP)
Unlock: Car theft, Market
Max Nerve: 75

Rank 3 – Enforcer (5,000 XP)
Unlock: PvP attacks, Bodyguard hiring
Max Nerve: 100

Rank 4 – Capo (25,000 XP)
Unlock: Join/Create Family, Safehouse
Max Nerve: 150

Rank 5 – Fixer (100,000 XP)
Unlock: Advanced properties
Max Nerve: 200

Rank 6 – Underboss (500,000 XP)
Unlock: Territory control
Max Nerve: 250

Rank 7 – Godfather (2,000,000 XP)
Unlock: Casino, Global leaderboard
Max Nerve: 300

Energy (Nerve):
- Regeneration: +1 every 180 seconds (configurable).
- Stored in Redis.

Heat:
- Range: 0–100.
- Increases with crimes.
- Reduces property income at thresholds.
- Prison mechanic introduced in Milestone 3.

--------------------------------------------------------------------
5. MILESTONES
--------------------------------------------------------------------

==============================
MILESTONE 1 — CORE & ECONOMY
==============================

Scope:
- Auth (Apple Sign-In + Email OTP, 18+ gate)
- Player profile
- Energy system (Redis)
- 3 PvE crimes
- Heat basic implementation
- XP + Rank
- Immutable Ledger system

Definition of Done:
- Player logs in.
- Spends Nerve.
- Executes crime.
- Earns CASH + XP.
- Rank progression works.
- Ledger passes reconciliation.

==============================
MILESTONE 2 — SYNDICATE
==============================

Scope:
- Family creation/join
- Role-based permissions (Don, Underboss, Capo, Soldier)
- Family Vault (10% automatic tax)
- SSE real-time chat
- Property system
- Daily passive income cron

Definition of Done:
- Families operational.
- Vault accumulates tax automatically.
- Properties generate daily income.
- Chat stable.

==============================
MILESTONE 3 — COMBAT & AI
==============================

Scope:
- Async PvP combat
- BULLET currency integration (deflation sink)
- Hospital/lockout system
- Anti-grief protections
- NPC Bodyguards
- AI Flavor Engine (JSON output only)

Definition of Done:
- PvP safe from double-spend and race conditions.
- BULLET consumption reduces global supply.
- AI text generation guarded and deterministic input-based.

==============================
MILESTONE 4 — LIVE OPS & RELEASE
==============================

Scope:
- StoreKit (DIAMOND packs)
- VIP subscription (QoL only)
- Moderation pipeline
- Report/abuse queue
- Weekly AI-driven events (flavor only)
- Season system
- AI Kill Switch (fallback to static text)
- App Store release preparation

Review Mode:
- Demo data seeder for reviewer navigation.
- No bypass of payment logic.
- No manipulation of monetization flow.

Definition of Done:
- IAP validated.
- Moderation active.
- Kill switch verified.
- App Store checklist complete.

--------------------------------------------------------------------
6. ECONOMY GOVERNANCE
--------------------------------------------------------------------

Weekly KPIs:
- D1 / D7 / D30 retention
- Inflation rate (CASH & BULLET supply)
- AI cost per action
- Ledger consistency
- Bullet sink ratio

Balancing:
- All economic constants configurable without redeploy.
- Code changes not required for balance tuning.

--------------------------------------------------------------------
7. CHANGE CONTROL
--------------------------------------------------------------------

- This file must be updated before any core system change.
- Rank table changes require version bump.
- Ledger schema change requires migration + audit.
- Monetization model changes require PO + Engineering approval.

--------------------------------------------------------------------

END OF DOCUMENT
