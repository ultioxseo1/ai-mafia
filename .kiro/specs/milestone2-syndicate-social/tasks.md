# Implementation Plan: AI MAFIA — Milestone 2 (Syndicate & Social)

## Overview

Implement the syndicate/social layer: Family CRUD with role hierarchy, shared Vault with automatic tax, purchasable Properties with daily passive income, and real-time SSE chat. Tasks are ordered by dependency: config extensions & data models first, then Ledger extensions (spend/transfer), then foundational services (FamilyVaultService, PropertyService, ChatService), then orchestrating service (FamilyService), then API layer (routers, deps), and finally the IncomeJob. All financial mutations flow through the immutable Ledger Service.

## Tasks

- [x] 1. ConfigService extensions and M2 game constants
  - [x] 1.1 Extend `services/api_fastapi/domain/services/config_service.py` with M2 keys
    - Add keys: `VAULT_TAX_RATE` (default 10), `MAX_FAMILY_MEMBERS` (default 25), `MAX_CAPO_COUNT` (default 3), `PROPERTY_DEFINITIONS` (JSON array with default property defs for speakeasy/casino/docks), `INCOME_JOB_SCHEDULE` (default "0 5 * * *"), `CHAT_HISTORY_LIMIT` (default 50), `CHAT_HEARTBEAT_INTERVAL` (default 30)
    - Add defaults to `_DEFAULTS` dict following existing pattern
    - _Requirements: 12.1, 12.2_

- [x] 2. Data models and Alembic migration
  - [x] 2.1 Create `services/api_fastapi/domain/models/family.py` with Family, FamilyMember, FamilyProperty models
    - `Family`: id, name (String 24), tag (String 5), status (FamilyStatus enum: ACTIVE/DISBANDED), created_at, disbanded_at
    - `FamilyMember`: id, family_id (FK families.id), player_id (FK players.id, unique), role (FamilyRole enum: SOLDIER/CAPO/UNDERBOSS/DON), joined_at
    - `FamilyProperty`: id, family_id (FK families.id), property_id (String 64), level (BigInteger, default 1), purchased_at, updated_at
    - `FamilyRole` string enum + `ROLE_RANK` numeric mapping dict (SOLDIER=1, CAPO=2, UNDERBOSS=3, DON=4)
    - `FamilyStatus` string enum (ACTIVE, DISBANDED)
    - CHECK constraints: name length 3-24, tag length 2-5, level >= 1
    - UniqueConstraint on FamilyMember.player_id, UniqueConstraint on (family_id, property_id)
    - Import `Base` from existing `economy.py`
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 3.1, 8.1, 9.1_

  - [x] 2.2 Create `services/api_fastapi/domain/models/chat.py` with ChatMessage model
    - `ChatMessage`: id, family_id (FK families.id), player_id (FK players.id), display_name (String 20), body (String 500), created_at
    - Index on (family_id, created_at) for history queries
    - CHECK constraint: body length 1-500
    - Import `Base` from existing `economy.py`
    - _Requirements: 7.2_

  - [x] 2.3 Create `PropertyDefinition` frozen dataclass in `services/api_fastapi/domain/models/family.py`
    - Fields: property_id (str), name (str), purchase_price (int), daily_income (int), max_level (int)
    - Add `load_property_definitions(config)` async helper that reads from ConfigService
    - _Requirements: 8.1_

  - [x] 2.4 Add `counterparty_owner_type` and `counterparty_owner_id` nullable columns to `LedgerEntry` model in `economy.py`
    - `counterparty_owner_type`: Optional[OwnerType], nullable
    - `counterparty_owner_id`: Optional[UUID], nullable
    - _Requirements: 6.1_

  - [x] 2.5 Create Alembic migration `002_milestone2_syndicate.py`
    - Add counterparty columns to `ledger_entries`
    - Create `families` table
    - Create `family_members` table
    - Create `family_properties` table
    - Create `chat_messages` table
    - Create partial unique indexes on `families.name` and `families.tag` WHERE `status = 'ACTIVE'`
    - _Requirements: 1.2, 1.3, 2.1, 7.2, 8.1_


  - [ ]* 2.6 Write property test for PropertyDefinition round-trip
    - **Property 21: Property definition round-trip**
    - **Validates: Requirements 8.1**

  - [ ]* 2.7 Write property test for family name and tag validation
    - **Property 2: Family name and tag validation**
    - **Validates: Requirements 1.2, 1.3**

- [x] 3. Checkpoint — Models and Migration
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Ledger Service extensions (spend and transfer)
  - [x] 4.1 Add `spend()` to `services/api_fastapi/domain/services/ledger_service.py`
    - Implement SPEND: lock wallet (SELECT FOR UPDATE), verify balance >= amount, decrease `wallet.balance`, append SPEND/POSTED ledger row
    - Full idempotency support (check/store idempotency key, fingerprint validation)
    - Raise `InsufficientFunds` if balance < amount
    - Follow same pattern as existing `earn()` and `reserve()`
    - _Requirements: 8.2, 9.1_

  - [x] 4.2 Add `transfer()` to `services/api_fastapi/domain/services/ledger_service.py`
    - Implement TRANSFER: lock source wallet, verify balance >= amount, decrease source balance, lock/create target wallet, increase target balance
    - Append two TRANSFER/POSTED ledger rows (one debit with counterparty=target, one credit with counterparty=source)
    - Use the new `counterparty_owner_type` and `counterparty_owner_id` columns
    - Full idempotency support
    - Raise `InsufficientFunds` if source balance < amount
    - _Requirements: 4.3, 6.1_

  - [ ]* 4.3 Write property test for wallet balance equals sum of posted entries (extended)
    - **Property 26: Wallet balance equals sum of posted ledger entries (extended)**
    - **Validates: Requirements 5.2, 5.3, 6.1, 8.2, 9.1**

- [x] 5. Checkpoint — Ledger Extensions
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Family Vault Service
  - [x] 6.1 Create `services/api_fastapi/domain/services/vault_service.py` with `FamilyVaultService` class
    - Constructor takes `ConfigService`
    - Implement `earn_with_tax(session, player_id, family_id, gross_amount, idempotency_key)`:
      - tax = floor(gross * vault_tax_rate / 100)
      - net = gross - tax
      - If tax > 0: call `ledger.earn()` for FAMILY vault (TAX entry, idem_key + ":tax") and `ledger.earn()` for PLAYER (EARN entry, idem_key + ":net")
      - If tax == 0: call `ledger.earn()` for PLAYER with full gross amount
      - All within caller's DB transaction
    - Implement `withdraw(session, actor_id, family_id, target_member_id, amount, idempotency_key)`:
      - Verify actor is Don of family
      - Verify target is current family member
      - Call `ledger.transfer()` from FAMILY wallet to PLAYER wallet
    - Implement `get_vault_balance(session, family_id)` → int
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 6.2 Write property test for tax invariant
    - **Property 12: Tax invariant — player net + vault tax == gross**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4**

  - [ ]* 6.3 Write property test for tax atomicity
    - **Property 13: Tax atomicity — both entries or neither**
    - **Validates: Requirements 5.5**

  - [ ]* 6.4 Write property test for vault withdrawal
    - **Property 14: Vault withdrawal transfers correct amount**
    - **Validates: Requirements 6.1**

  - [ ]* 6.5 Write property test for Don-only actions
    - **Property 15: Don-only actions reject non-Don actors**
    - **Validates: Requirements 6.3, 8.4, 9.6**

  - [ ]* 6.6 Write property test for insufficient vault funds
    - **Property 16: Insufficient vault funds rejects over-balance operations**
    - **Validates: Requirements 6.2, 8.3, 9.5**

- [x] 7. Family Service
  - [x] 7.1 Create `services/api_fastapi/domain/services/family_service.py` with `FamilyService` class
    - Constructor takes `redis`, `ConfigService`
    - Implement `create_family(session, player_id, name, tag, idempotency_key)`:
      - Check player rank >= Capo (query Player model)
      - Check player not already in a family (query FamilyMember)
      - Validate name: `^[a-zA-Z0-9_ ]{3,24}$`, unique among ACTIVE families
      - Validate tag: `^[A-Z0-9]{2,5}$`, unique among ACTIVE families
      - Create Family record (status=ACTIVE), FamilyMember (role=DON)
      - Create FAMILY Wallet via `ledger._get_or_create_wallet`
    - Implement `join_family(session, player_id, family_id, idempotency_key)`:
      - Check rank >= Capo, not already in family, family not full (MAX_FAMILY_MEMBERS)
      - Add as Soldier
    - Implement `leave_family(session, player_id, idempotency_key)`:
      - If Don with other members → reject (don_must_transfer_or_disband)
      - Remove from roster (DELETE row)
    - Implement `kick_member(session, actor_id, target_id, idempotency_key)`:
      - Verify actor.role > target.role (numeric ROLE_RANK comparison)
      - Remove target from roster
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 11.1, 11.2, 11.3_

  - [x] 7.2 Add role management methods to `FamilyService`
    - Implement `promote_member(session, actor_id, target_id, new_role, idempotency_key)`:
      - Only Don can promote
      - Soldier→Capo: check capo count < MAX_CAPO_COUNT
      - Capo→Underboss: check no Underboss exists
      - Update member role
    - Implement `demote_member(session, actor_id, target_id, new_role, idempotency_key)`:
      - actor.role > target.role required
      - Don or Underboss can demote Capo→Soldier
      - Only Don can demote Underboss→Capo
    - Implement `transfer_don(session, actor_id, target_id, idempotency_key)`:
      - Verify actor is current Don
      - Assign DON to target, demote former Don to Underboss (or Capo if Underboss occupied)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

  - [x] 7.3 Add dissolution and query methods to `FamilyService`
    - Implement `disband_family(session, player_id, idempotency_key)`:
      - Verify player is Don, no other members remain
      - Transfer vault balance to Don via `ledger.transfer()`
      - Mark family as DISBANDED, set disbanded_at, remove Don from roster
    - Implement `get_family(session, family_id)`, `get_player_family(session, player_id)`, `list_members(session, family_id)`
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [ ]* 7.4 Write property test for family creation
    - **Property 1: Family creation produces Don + zero-balance vault**
    - **Validates: Requirements 1.1**

  - [ ]* 7.5 Write property test for rank gate
    - **Property 3: Rank gate rejects below-Capo players**
    - **Validates: Requirements 1.4, 2.2, 11.1, 11.2, 11.3**

  - [ ]* 7.6 Write property test for one family per player
    - **Property 4: One family per player invariant**
    - **Validates: Requirements 1.5, 2.3**

  - [ ]* 7.7 Write property test for join
    - **Property 5: Join adds as Soldier and respects capacity**
    - **Validates: Requirements 2.1, 2.4**

  - [ ]* 7.8 Write property test for leave
    - **Property 6: Non-Don members can leave freely**
    - **Validates: Requirements 2.5**

  - [ ]* 7.9 Write property test for kick
    - **Property 7: Kick requires strictly higher role**
    - **Validates: Requirements 2.7, 2.8, 2.9, 2.10**

  - [ ]* 7.10 Write property test for promotion
    - **Property 8: Promotion respects role limits and authority**
    - **Validates: Requirements 3.1, 3.2, 3.5, 3.6**

  - [ ]* 7.11 Write property test for demotion
    - **Property 9: Demotion respects role authority**
    - **Validates: Requirements 3.3, 3.4, 3.6**

  - [ ]* 7.12 Write property test for Don transfer
    - **Property 10: Don transfer assigns Don and demotes former Don**
    - **Validates: Requirements 3.7**

  - [ ]* 7.13 Write property test for disband
    - **Property 11: Disband requires sole member and transfers vault**
    - **Validates: Requirements 4.1, 4.2, 4.3**

- [x] 8. Checkpoint — Family and Vault Services
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Property Service
  - [x] 9.1 Create `services/api_fastapi/domain/services/property_service.py` with `PropertyService` class
    - Constructor takes `ConfigService`
    - Implement `purchase_property(session, actor_id, family_id, property_id, idempotency_key)`:
      - Verify actor is Don
      - Load PropertyDefinition from config
      - Check family doesn't already own this property (query FamilyProperty)
      - Call `ledger.spend(FAMILY, family_id, CASH, purchase_price, idem_key)`
      - Create FamilyProperty record at level 1
    - Implement `upgrade_property(session, actor_id, family_id, property_id, idempotency_key)`:
      - Verify actor is Don
      - Load ownership record, check not at max_level
      - cost = purchase_price * current_level
      - Call `ledger.spend(FAMILY, family_id, CASH, cost, idem_key)`
      - Increment level, update updated_at
    - Implement `calculate_daily_income(session, family_id)` → int: sum of (base_daily_income * level) for all owned properties
    - Implement `list_properties(config)` → List[PropertyDefinition]
    - Implement `list_family_properties(session, family_id)` → List[FamilyProperty]
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_

  - [ ]* 9.2 Write property test for property cost and income formulas
    - **Property 22: Property cost and income formulas**
    - **Validates: Requirements 9.2, 9.3**

  - [ ]* 9.3 Write property test for property purchase
    - **Property 20: Property purchase creates level-1 ownership**
    - **Validates: Requirements 8.2, 8.5**

  - [ ]* 9.4 Write property test for property upgrade
    - **Property 23: Property upgrade increments level and deducts correct cost**
    - **Validates: Requirements 9.1, 9.4**

- [x] 10. Chat Service
  - [x] 10.1 Create `services/api_fastapi/domain/services/chat_service.py` with `ChatService` class
    - Constructor takes `redis`, `ConfigService`
    - Implement `send_message(session, player_id, family_id, body)`:
      - Validate body length 1-500 chars, reject with "invalid_message_length" if invalid
      - Persist ChatMessage to DB (player_id, family_id, display_name, body, created_at)
      - Publish JSON to Redis PubSub channel `family_chat:{family_id}`
    - Implement `get_history(session, family_id, limit=None)`:
      - Query most recent N messages (default from CHAT_HISTORY_LIMIT config)
      - Order by created_at descending
    - Implement `subscribe(family_id)` → AsyncGenerator[ChatEvent, None]:
      - Subscribe to Redis PubSub channel `family_chat:{family_id}`
      - Yield SSE events from channel messages
      - Include periodic heartbeat events (CHAT_HEARTBEAT_INTERVAL config)
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [ ]* 10.2 Write property test for chat message validation
    - **Property 17: Chat message validation and persistence**
    - **Validates: Requirements 7.2, 7.4**

  - [ ]* 10.3 Write property test for chat history
    - **Property 18: Chat history returns most recent N messages in order**
    - **Validates: Requirements 7.6**

  - [ ]* 10.4 Write property test for chat access control
    - **Property 19: Chat access restricted to family members**
    - **Validates: Requirements 7.5**

- [x] 11. Checkpoint — Property and Chat Services
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Wire CrimeService to use FamilyVaultService for tax
  - [x] 12.1 Update `services/api_fastapi/domain/services/crime_service.py` to integrate vault tax
    - Inject `FamilyVaultService` into CrimeService constructor
    - In `execute_crime()`: after calculating CASH reward, check if player is in a family
    - If in family: call `vault_service.earn_with_tax()` instead of `ledger.earn()` directly
    - If not in family: call `ledger.earn()` as before (no tax)
    - Update idempotency key handling to pass through to vault service
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [x] 13. API Routers and Dependency Injection
  - [x] 13.1 Extend `services/api_fastapi/api/deps.py` with M2 service factories
    - Add `get_family_service()` → FamilyService
    - Add `get_vault_service()` → FamilyVaultService
    - Add `get_property_service()` → PropertyService
    - Add `get_chat_service()` → ChatService
    - Add `require_family_membership(player_id, session)` dependency that loads player's FamilyMember or raises `not_in_family`
    - Follow existing DI pattern with `Depends()`
    - _Requirements: all M2 endpoints_

  - [x] 13.2 Create `services/api_fastapi/api/routers/family_router.py`
    - `POST /families` → `FamilyService.create_family` (JWT + age gate, idempotency)
    - `GET /families/{family_id}` → `FamilyService.get_family` (JWT + age gate)
    - `GET /families/me` → `FamilyService.get_player_family` (JWT + age gate)
    - `POST /families/{family_id}/join` → `FamilyService.join_family` (JWT + age gate, idempotency)
    - `POST /families/me/leave` → `FamilyService.leave_family` (JWT + age gate, idempotency)
    - `POST /families/me/kick` → `FamilyService.kick_member` (JWT + age gate, idempotency)
    - `POST /families/me/promote` → `FamilyService.promote_member` (JWT + age gate, idempotency)
    - `POST /families/me/demote` → `FamilyService.demote_member` (JWT + age gate, idempotency)
    - `POST /families/me/transfer-don` → `FamilyService.transfer_don` (JWT + age gate, idempotency)
    - `POST /families/me/disband` → `FamilyService.disband_family` (JWT + age gate, idempotency)
    - Wire error handlers for all family error codes
    - _Requirements: 1.1, 2.1, 2.5, 2.7, 3.1, 3.3, 3.7, 4.1, 13.1_

  - [x] 13.3 Create `services/api_fastapi/api/routers/vault_router.py`
    - `GET /families/me/vault` → `FamilyVaultService.get_vault_balance` (JWT + age gate + family membership)
    - `POST /families/me/vault/withdraw` → `FamilyVaultService.withdraw` (JWT + age gate + family membership, idempotency)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 13.4 Create `services/api_fastapi/api/routers/property_router.py`
    - `GET /properties` → `PropertyService.list_properties` (JWT + age gate)
    - `GET /families/me/properties` → `PropertyService.list_family_properties` (JWT + age gate + family membership)
    - `POST /families/me/properties/{property_id}/purchase` → `PropertyService.purchase_property` (JWT + age gate + family membership, idempotency)
    - `POST /families/me/properties/{property_id}/upgrade` → `PropertyService.upgrade_property` (JWT + age gate + family membership, idempotency)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 9.1, 9.4, 9.5, 9.6, 9.7_

  - [x] 13.5 Create `services/api_fastapi/api/routers/chat_router.py`
    - `GET /families/me/chat` → `ChatService.subscribe` (JWT + age gate + family membership, SSE streaming response)
    - `POST /families/me/chat` → `ChatService.send_message` (JWT + age gate + family membership)
    - `GET /families/me/chat/history` → `ChatService.get_history` (JWT + age gate + family membership)
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [x] 13.6 Register M2 routers and error handlers in `services/api_fastapi/api/app.py`
    - Include family_router, vault_router, property_router, chat_router
    - Add error handlers for all 14 new M2 error codes
    - _Requirements: all M2 endpoints_

  - [ ]* 13.7 Write property test for idempotency on M2 endpoints
    - **Property 27: Idempotency on all M2 state-changing endpoints**
    - **Validates: Requirements 1.6, 2.11, 3.8, 4.4, 5.6, 6.5, 8.6, 9.7, 13.1, 13.2, 13.3**

- [x] 14. Checkpoint — API Layer
  - Ensure all tests pass, ask the user if questions arise.

- [x] 15. Income Job
  - [x] 15.1 Create `services/api_fastapi/domain/jobs/income_job.py` with `IncomeJob` class
    - Constructor takes `session`, `ConfigService`
    - Implement `run()` → IncomeReport:
      - Query all active families with at least one FamilyProperty (JOIN families + family_properties WHERE status=ACTIVE)
      - For each family: total_income = sum(base_daily_income * level) for each property (load PropertyDefinitions from config, match by property_id)
      - Call `ledger.earn(FAMILY, family_id, CASH, total_income, idem_key=f"income:{family_id}:{date}")` with date-scoped idempotency key
      - On per-family error: log error and continue processing remaining families
      - Log summary: families_processed, total_distributed, execution_time
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [ ]* 15.2 Write property test for income job credits
    - **Property 24: Income job credits correct total per family**
    - **Validates: Requirements 10.2, 10.3**

  - [ ]* 15.3 Write property test for income job resilience
    - **Property 25: Income job resilience — per-family errors don't halt processing**
    - **Validates: Requirements 10.5**

- [x] 16. Final Checkpoint — Full Integration
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate the 27 correctness properties from the design document
- All services use Python 3.12 async with FastAPI, SQLAlchemy 2.0, and aioredis
- The existing Ledger Service and economy models are extended, not rewritten
- Family Vaults are Wallets with `owner_type=FAMILY` — no new financial model needed
- CrimeService integration (task 12) wires the tax system into the existing M1 crime loop
