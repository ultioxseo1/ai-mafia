# Implementation Plan: AI MAFIA — Milestone 1 (Core & Economy)

## Overview

Implement the foundational gameplay loop: authenticate → create profile → spend Nerve → execute crime → earn CASH + XP → rank up. Tasks are ordered by dependency: config & models first, then foundational services (Nerve, Heat, Rank), then orchestrating services (Crime, Auth), then API layer (middleware, routers), and finally the reconciliation job. The existing Ledger Service and economy models are extended, not rewritten.

## Tasks

- [x] 1. Config Service and Game Constants
  - [x] 1.1 Create `services/api_fastapi/domain/services/config_service.py` with `ConfigService` class
    - Implement `get(key)` that checks Redis override first, falls back to env var
    - Define all game constant keys: `NERVE_REGEN_INTERVAL`, `HEAT_DECAY_INTERVAL`, `CRIME_DEFINITIONS`, `RECONCILIATION_SCHEDULE`, rank table, etc.
    - Use `aioredis` for Redis reads, `os.environ` for env fallback
    - _Requirements: 12.1, 12.2_

  - [ ]* 1.2 Write property test for Config Service hot-reload
    - **Property 24: Configuration hot-reload**
    - **Validates: Requirements 12.2**

- [x] 2. Player Model and Alembic Migration
  - [x] 2.1 Create `services/api_fastapi/domain/models/player.py` with `Player` SQLAlchemy model
    - Fields: `id`, `apple_sub`, `email`, `display_name`, `rank`, `xp`, `age_confirmed`, `is_active`, `created_at`, `updated_at`
    - CHECK constraints: `xp >= 0`, `display_name` length 3–20
    - Unique indexes on `apple_sub`, `email`, `display_name`
    - Import `Base` from existing `economy.py`
    - _Requirements: 1.2, 2.3, 4.1, 4.2, 8.4_

  - [x] 2.2 Create `CrimeDefinition` frozen dataclass in `services/api_fastapi/domain/models/crime.py`
    - Fields: `crime_id`, `name`, `nerve_cost`, `cash_min`, `cash_max`, `xp_reward`, `heat_increase`
    - Add a loader function that reads 3 crime definitions from ConfigService
    - _Requirements: 6.1_

  - [x] 2.3 Create Alembic migration for the `players` table
    - Generate migration from the Player model
    - _Requirements: 1.2, 2.3, 4.1_

- [x] 3. Extend Ledger Service with EARN entry type
  - [x] 3.1 Add `earn()` function to `services/api_fastapi/domain/services/ledger_service.py`
    - Implement EARN: lock wallet (SELECT FOR UPDATE), increase `wallet.balance`, append POSTED ledger row with `entry_type=EARN`
    - Full idempotency support (check/store idempotency key, fingerprint validation)
    - Follow the same pattern as existing `reserve()`/`capture()`/`release()`
    - _Requirements: 9.1, 9.7_

  - [ ]* 3.2 Write property test for ledger entry amounts
    - **Property 20: Ledger entry amounts are always positive**
    - **Validates: Requirements 9.3**

  - [ ]* 3.3 Write property test for wallet balance equals sum of posted entries
    - **Property 19: Wallet balance equals sum of posted ledger entries**
    - **Validates: Requirements 9.1, 9.2, 10.2, 10.3**

  - [ ]* 3.4 Write property test for idempotency replay
    - **Property 21: Idempotency replay returns cached result**
    - **Validates: Requirements 9.4, 11.3**

  - [ ]* 3.5 Write property test for idempotency conflict
    - **Property 22: Idempotency conflict on payload mismatch**
    - **Validates: Requirements 9.5**

- [x] 4. Checkpoint — Models and Ledger
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Nerve Service (Redis)
  - [x] 5.1 Create `services/api_fastapi/domain/services/nerve_service.py` with `NerveService` class
    - Implement `get_nerve(player_id)` with lazy regeneration formula: `min(stored + floor((now - last_update) / regen_interval), cap)`
    - Implement `consume_nerve(player_id, amount)` using Redis Lua script for atomicity
    - Implement `restore_nerve(player_id, amount)` for compensation on failed transactions
    - Implement `update_cap(player_id, new_cap)` for rank promotion
    - Initialize nerve state on first access with default cap from ConfigService
    - Redis key: `nerve:{player_id}` → Hash `{value, last_update, cap}`
    - Read `NERVE_REGEN_INTERVAL` from ConfigService
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [ ]* 5.2 Write property test for nerve lazy regeneration formula
    - **Property 8: Nerve lazy regeneration formula**
    - **Validates: Requirements 5.2, 5.3, 5.4**

  - [ ]* 5.3 Write property test for nerve consumption atomicity
    - **Property 10: Nerve consumption atomicity**
    - **Validates: Requirements 5.6, 5.7**

  - [ ]* 5.4 Write property test for nerve cap updates on rank promotion
    - **Property 9: Nerve cap updates on rank promotion**
    - **Validates: Requirements 5.5**

- [x] 6. Heat Service (Redis)
  - [x] 6.1 Create `services/api_fastapi/domain/services/heat_service.py` with `HeatService` class
    - Implement `get_heat(player_id)` with lazy decay formula: `max(stored - floor((now - last_update) / decay_interval), 0)`
    - Implement `add_heat(player_id, amount)` with atomic Redis operation, clamp at 100
    - Redis key: `heat:{player_id}` → Hash `{value, last_update}`
    - Read `HEAT_DECAY_INTERVAL` from ConfigService
    - _Requirements: 7.1, 7.2, 7.4, 7.5_

  - [ ]* 6.2 Write property test for heat invariant
    - **Property 14: Heat invariant — always in [0, 100]**
    - **Validates: Requirements 7.1, 7.2, 7.5**

  - [ ]* 6.3 Write property test for heat lazy decay formula
    - **Property 15: Heat lazy decay formula**
    - **Validates: Requirements 7.4**

- [x] 7. Rank Service
  - [x] 7.1 Create `services/api_fastapi/domain/services/rank_service.py` with `RankService` class
    - Define locked `RANK_TABLE` constant: Empty-Suit(0,50), Runner(1000,75), Enforcer(5000,100), Capo(25000,150), Fixer(100000,200), Underboss(500000,250), Godfather(2000000,300)
    - Implement pure `compute_rank(total_xp)` → `(rank_name, nerve_cap)` supporting multi-rank jumps
    - Implement `award_xp(session, player_id, xp, idem_key)` that adds XP, checks promotion, updates nerve cap via NerveService if promoted
    - XP stored as cumulative BigInteger, only increases
    - Rank change persisted in same DB transaction as XP award
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [ ]* 7.2 Write property test for rank computation from XP
    - **Property 16: Rank computation from XP**
    - **Validates: Requirements 8.2, 8.5**

  - [ ]* 7.3 Write property test for rank promotion updates nerve cap
    - **Property 17: Rank promotion updates nerve cap**
    - **Validates: Requirements 8.3**

  - [ ]* 7.4 Write property test for XP monotonicity
    - **Property 18: XP is monotonically non-decreasing**
    - **Validates: Requirements 8.4**

- [x] 8. Checkpoint — Foundational Services
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Player Profile Service
  - [x] 9.1 Create `services/api_fastapi/domain/services/profile_service.py` with `PlayerProfileService` class
    - Implement `create_profile(player_id)` initializing defaults: rank="Empty-Suit", xp=0, heat=0, nerve=50
    - Implement `update_display_name(player_id, name, idem_key)` with validation: 3–20 chars, `^[a-zA-Z0-9_]+$`, unique check
    - Implement `get_profile(player_id)` aggregating: Player record + wallet balance (PG) + nerve state (Redis) + heat (Redis)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [ ]* 9.2 Write property test for display name validation
    - **Property 6: Display name validation**
    - **Validates: Requirements 4.2**

  - [ ]* 9.3 Write property test for profile response fields
    - **Property 7: Profile response contains all required fields**
    - **Validates: Requirements 4.4**

- [x] 10. Auth Service
  - [x] 10.1 Create `services/api_fastapi/domain/services/auth_service.py` with `AuthService` class
    - Implement `apple_sign_in(identity_token)`: verify with Apple servers, create-or-fetch player, create CASH wallet, return JWT
    - Implement `request_otp(email)`: generate 6-digit code, store in Redis with 600s TTL, rate limit (5 per 15min), send email
    - Implement `verify_otp(email, code)`: validate OTP, create-or-fetch player, create CASH wallet, return JWT
    - Implement `confirm_age(player_id, confirmed)`: persist `age_confirmed` on player record, deny access if not confirmed
    - Handle errors: `invalid_token`, `upstream_unavailable`, `invalid_otp`, `otp_expired`, `rate_limited`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3_

  - [ ]* 10.2 Write property test for first-time auth creates player and wallet
    - **Property 1: First-time authentication creates player and zero-balance wallet**
    - **Validates: Requirements 1.2, 2.3**

  - [ ]* 10.3 Write property test for invalid credentials rejection
    - **Property 2: Invalid credentials are rejected with correct error codes**
    - **Validates: Requirements 1.3, 2.4**

  - [ ]* 10.4 Write property test for OTP generation
    - **Property 3: OTP generation produces valid 6-digit codes**
    - **Validates: Requirements 2.1**

  - [ ]* 10.5 Write property test for OTP round-trip
    - **Property 4: OTP round-trip authentication**
    - **Validates: Requirements 2.2**

  - [ ]* 10.6 Write property test for age gate
    - **Property 5: Age gate blocks unconfirmed players**
    - **Validates: Requirements 3.1, 3.3**

- [x] 11. Crime Service
  - [x] 11.1 Create `services/api_fastapi/domain/services/crime_service.py` with `CrimeService` class
    - Implement `execute_crime(player_id, crime_id, idempotency_key)` orchestrating the full pipeline:
      1. Check idempotency (return cached if replay)
      2. Consume Nerve via NerveService (Redis)
      3. Calculate CASH reward as `random.randint(cash_min, cash_max)`
      4. In single PG transaction: Ledger EARN (credit CASH), award XP via RankService (check rank promotion)
      5. Add Heat via HeatService (Redis, after PG commit)
      6. Store idempotency result
    - On PG failure: restore Nerve via `NerveService.restore_nerve()`
    - Implement `list_crimes()` returning all 3 crime definitions from config
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9_

  - [ ]* 11.2 Write property test for crime CASH reward bounds
    - **Property 11: Crime CASH reward is within configured bounds**
    - **Validates: Requirements 6.3**

  - [ ]* 11.3 Write property test for crime execution state changes
    - **Property 12: Crime execution produces correct state changes**
    - **Validates: Requirements 6.4, 6.5, 6.6**

  - [ ]* 11.4 Write property test for insufficient nerve prevents state changes
    - **Property 13: Insufficient nerve prevents all state changes**
    - **Validates: Requirements 6.8**

- [x] 12. Checkpoint — All Domain Services
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Idempotency Middleware
  - [x] 13.1 Create `services/api_fastapi/middleware/idempotency.py` as a FastAPI middleware/dependency
    - Extract `Idempotency-Key` header from all state-changing requests (POST, PUT, PATCH, DELETE)
    - Return 400 `missing_idempotency_key` error if header is absent on state-changing methods
    - Pass the key downstream via request state for service methods to consume
    - Allow GET/HEAD/OPTIONS requests through without the header
    - _Requirements: 11.1, 11.2_

  - [ ]* 13.2 Write property test for missing idempotency key rejection
    - **Property 23: Missing idempotency key rejection**
    - **Validates: Requirements 11.1**

- [x] 14. API Routers and Endpoint Wiring
  - [x] 14.1 Create `services/api_fastapi/api/routers/auth_router.py`
    - `POST /auth/apple` → `AuthService.apple_sign_in`
    - `POST /auth/otp/request` → `AuthService.request_otp`
    - `POST /auth/otp/verify` → `AuthService.verify_otp`
    - `POST /auth/age-confirm` → `AuthService.confirm_age` (JWT required)
    - Wire idempotency key from middleware
    - _Requirements: 1.1, 2.1, 2.2, 3.1_

  - [x] 14.2 Create `services/api_fastapi/api/routers/profile_router.py`
    - `GET /profile/me` → `PlayerProfileService.get_profile` (JWT required)
    - `PUT /profile/me/name` → `PlayerProfileService.update_display_name` (JWT required, idempotency key)
    - _Requirements: 4.2, 4.4_

  - [x] 14.3 Create `services/api_fastapi/api/routers/crime_router.py`
    - `POST /crimes/{crime_id}/execute` → `CrimeService.execute_crime` (JWT required, idempotency key)
    - `GET /crimes` → `CrimeService.list_crimes` (JWT required)
    - _Requirements: 6.1, 6.2_

  - [x] 14.4 Create `services/api_fastapi/api/routers/nerve_router.py`
    - `GET /nerve` → `NerveService.get_nerve` (JWT required)
    - _Requirements: 5.1_

  - [x] 14.5 Wire all routers and middleware into the FastAPI app entrypoint
    - Register idempotency middleware
    - Include auth, profile, crime, nerve routers
    - Add JWT authentication dependency
    - Add age-gate dependency that blocks unconfirmed players from gameplay endpoints
    - Configure error handlers for all error codes in the error catalog
    - _Requirements: 3.1, 3.2, 11.1_

- [x] 15. Checkpoint — API Layer
  - Ensure all tests pass, ask the user if questions arise.

- [x] 16. Reconciliation Job
  - [x] 16.1 Create `services/api_fastapi/domain/jobs/reconciliation.py` with `ReconciliationJob` class
    - Implement `run()` that iterates all wallets, compares `wallet.balance` to SUM of POSTED ledger entries (EARN/CAPTURE as credits, SPEND/TAX as debits)
    - On mismatch: emit SEV-1 alert to configured alerting channel
    - Log summary: wallets_checked, mismatches, execution_time
    - Schedule via configurable cron (read from ConfigService)
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [ ]* 16.2 Write integration test for reconciliation job
    - Test with matching balances (no alert)
    - Test with intentional mismatch (SEV-1 alert emitted)
    - Test summary output format
    - _Requirements: 10.2, 10.3, 10.4_

- [x] 17. Test Infrastructure Setup
  - [x] 17.1 Create `tests/conftest.py` with shared fixtures
    - Async PostgreSQL test session (using test database)
    - Redis mock or test Redis instance
    - Player factory fixture
    - Wallet factory fixture
    - _Requirements: all_

- [x] 18. Final Checkpoint — Full Integration
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate the 24 correctness properties from the design document
- The existing Ledger Service (`ledger_service.py`) and economy models (`economy.py`) are extended, not rewritten
- All services use Python 3.12 async with FastAPI, SQLAlchemy 2.0, and aioredis
- Nerve and Heat use lazy computation in Redis — no background workers needed
