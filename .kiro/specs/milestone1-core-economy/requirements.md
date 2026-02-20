# Requirements Document — AI MAFIA: Milestone 1 (Core & Economy)

## Introduction

Milestone 1 establishes the foundational gameplay loop for AI MAFIA: a 1930s neo-noir mobile mafia game. This milestone delivers authentication, player profiles, the energy (Nerve) system, three PvE crimes, Heat tracking, XP/Rank progression, and the immutable ledger economy. Upon completion a player can log in, spend Nerve, execute a crime, earn CASH + XP, rank up, and the ledger passes daily reconciliation.

## Glossary

- **Backend**: The FastAPI (Python 3.12 async) server that handles all game logic and state mutations.
- **Client**: The Flutter iOS-first mobile application.
- **Auth_Service**: The backend subsystem responsible for authentication, token issuance, and age verification.
- **Player_Profile_Service**: The backend subsystem that manages player profile CRUD and state.
- **Nerve_Service**: The backend subsystem backed by Redis that manages Nerve (energy) regeneration, consumption, and cap enforcement.
- **Crime_Service**: The backend subsystem that orchestrates PvE crime execution, reward calculation, and outcome resolution.
- **Heat_Service**: The backend subsystem that tracks and manages a player's Heat value (0–100).
- **Rank_Service**: The backend subsystem that manages XP accumulation and rank promotion using the locked rank table.
- **Ledger_Service**: The existing append-only immutable ledger subsystem that processes all economy mutations via RESERVE → CAPTURE → RELEASE.
- **Reconciliation_Job**: A scheduled daily job that verifies wallet balances match the sum of posted ledger entries.
- **Wallet**: A database record holding a player's balance and reserved balance for a given currency.
- **Nerve**: The energy resource spent to perform actions. Regenerates at +1 every 180 seconds, capped per rank.
- **Heat**: A 0–100 integer scale representing law-enforcement attention on a player. Increases when crimes are committed.
- **XP**: Experience points earned from actions, driving rank progression.
- **Rank**: One of seven progression tiers (Empty-Suit through Godfather) with locked XP thresholds and Nerve caps.
- **CASH**: The soft (in-game) currency earned from crimes and spent on in-game goods.
- **Idempotency_Key**: A unique key scoped to (owner_type, owner_id, action) that prevents duplicate state mutations on retry.
- **PvE_Crime**: A button-based solo crime action that costs Nerve and yields CASH, XP, and Heat.

## Requirements

### Requirement 1: Apple Sign-In Authentication

**User Story:** As a new player, I want to sign in with my Apple ID, so that I can create an account quickly on iOS.

#### Acceptance Criteria

1. WHEN a player initiates Apple Sign-In with a valid Apple identity token, THE Auth_Service SHALL verify the token with Apple's servers and return a session JWT within 3 seconds.
2. WHEN a player signs in for the first time with a valid Apple identity token, THE Auth_Service SHALL create a new player record and associated CASH Wallet with a zero balance.
3. IF the Apple identity token is invalid or expired, THEN THE Auth_Service SHALL reject the request with an "invalid_token" error and a human-readable message.
4. IF the Apple identity token verification call to Apple's servers fails due to a network error, THEN THE Auth_Service SHALL return a retriable "upstream_unavailable" error.

### Requirement 2: Email OTP Authentication

**User Story:** As a new player without an Apple device, I want to sign in with my email via a one-time password, so that I have an alternative authentication path.

#### Acceptance Criteria

1. WHEN a player requests an OTP for a valid email address, THE Auth_Service SHALL generate a 6-digit numeric code, store it with a 10-minute TTL, and send it to the provided email address.
2. WHEN a player submits a valid OTP that matches the stored code and has not expired, THE Auth_Service SHALL authenticate the player and return a session JWT.
3. WHEN a player submits a valid OTP for the first time (no existing account), THE Auth_Service SHALL create a new player record and associated CASH Wallet with a zero balance.
4. IF a player submits an incorrect OTP, THEN THE Auth_Service SHALL reject the request with an "invalid_otp" error.
5. IF a player submits an OTP after the 10-minute TTL has elapsed, THEN THE Auth_Service SHALL reject the request with an "otp_expired" error.
6. IF a player requests more than 5 OTPs for the same email address within a 15-minute window, THEN THE Auth_Service SHALL reject the request with a "rate_limited" error.

### Requirement 3: Age Gate (18+)

**User Story:** As the game operator, I want to enforce an 18+ age gate during onboarding, so that the game complies with age-restriction policies.

#### Acceptance Criteria

1. WHEN a new player completes authentication for the first time, THE Auth_Service SHALL require the player to confirm they are 18 years of age or older before granting access to gameplay.
2. IF a player declines the 18+ confirmation, THEN THE Auth_Service SHALL deny access to gameplay and display a message explaining the age requirement.
3. THE Auth_Service SHALL persist the age-confirmation status on the player record so that returning players are not prompted again.

### Requirement 4: Player Profile

**User Story:** As a player, I want to have a profile with a display name and visible stats, so that I can track my progress and identity in the game.

#### Acceptance Criteria

1. WHEN a new player record is created, THE Player_Profile_Service SHALL initialize the profile with default values: Rank "Empty-Suit", XP 0, Heat 0, and Nerve at the rank-1 maximum (50).
2. WHEN a player sets or updates a display name, THE Player_Profile_Service SHALL validate that the name is between 3 and 20 characters, contains only alphanumeric characters and underscores, and is unique across all players.
3. IF a player submits a display name that is already taken, THEN THE Player_Profile_Service SHALL reject the request with a "name_taken" error.
4. WHEN a player requests their profile, THE Player_Profile_Service SHALL return the current display name, Rank, XP, Heat, CASH balance, and current Nerve with the next-regeneration timestamp.
5. THE Player_Profile_Service SHALL include an idempotency_key on the display-name update endpoint to prevent duplicate mutations on retry.

### Requirement 5: Nerve (Energy) System

**User Story:** As a player, I want my Nerve to regenerate over time up to my rank cap, so that I can return to the game and have energy to perform actions.

#### Acceptance Criteria

1. THE Nerve_Service SHALL store each player's current Nerve value and last-regeneration timestamp in Redis.
2. THE Nerve_Service SHALL regenerate Nerve at a rate of +1 every 180 seconds (configurable without code change).
3. WHILE a player's Nerve is below the maximum for the player's current Rank, THE Nerve_Service SHALL continue regenerating Nerve on each read or tick until the cap is reached.
4. WHILE a player's Nerve is at or above the maximum for the player's current Rank, THE Nerve_Service SHALL stop regenerating Nerve.
5. WHEN a player's Rank increases and the new Rank has a higher Nerve cap, THE Nerve_Service SHALL allow Nerve to continue regenerating up to the new cap.
6. WHEN a game action consumes Nerve, THE Nerve_Service SHALL atomically decrement the player's Nerve value and reject the operation if the player has insufficient Nerve.
7. IF a Nerve consumption request specifies an amount greater than the player's current Nerve, THEN THE Nerve_Service SHALL reject the request with an "insufficient_nerve" error.

### Requirement 6: PvE Crime Execution

**User Story:** As a player, I want to execute PvE crimes to earn CASH and XP, so that I can progress through the game.

#### Acceptance Criteria

1. THE Crime_Service SHALL support exactly three PvE crimes in Milestone 1, each defined with a configurable Nerve cost, base CASH reward range (min/max integers), base XP reward, and base Heat increase.
2. WHEN a player executes a PvE crime, THE Crime_Service SHALL deduct the crime's Nerve cost from the player's Nerve via the Nerve_Service.
3. WHEN a player executes a PvE crime successfully, THE Crime_Service SHALL calculate the CASH reward as a random integer within the crime's configured min/max range (inclusive).
4. WHEN a player executes a PvE crime successfully, THE Crime_Service SHALL credit the calculated CASH reward to the player's CASH Wallet via the Ledger_Service using an EARN entry.
5. WHEN a player executes a PvE crime successfully, THE Crime_Service SHALL award the crime's configured base XP to the player via the Rank_Service.
6. WHEN a player executes a PvE crime successfully, THE Crime_Service SHALL increase the player's Heat by the crime's configured Heat value via the Heat_Service.
7. THE Crime_Service SHALL enforce idempotency on each crime execution request using the provided idempotency_key, so that retried requests produce the same outcome.
8. IF the player does not have enough Nerve to cover the crime's cost, THEN THE Crime_Service SHALL reject the request with an "insufficient_nerve" error without modifying any state.
9. IF any step in the crime execution pipeline fails after Nerve has been deducted, THEN THE Crime_Service SHALL roll back all partial state changes within the same database transaction.

### Requirement 7: Heat System

**User Story:** As a player, I want my Heat to increase when I commit crimes, so that there is a risk/reward dynamic to my actions.

#### Acceptance Criteria

1. THE Heat_Service SHALL maintain each player's Heat as an integer value in the range 0–100 (inclusive).
2. WHEN a crime increases a player's Heat, THE Heat_Service SHALL add the crime's Heat value to the player's current Heat, capping at 100.
3. THE Heat_Service SHALL expose the player's current Heat value via the player profile endpoint.
4. WHILE a player's Heat is above 0, THE Heat_Service SHALL decay Heat by 1 point every 300 seconds (configurable without code change).
5. IF a Heat increase would cause the value to exceed 100, THEN THE Heat_Service SHALL clamp the value to 100.

### Requirement 8: XP and Rank Progression

**User Story:** As a player, I want to earn XP and rank up through seven tiers, so that I unlock new game features and higher Nerve caps.

#### Acceptance Criteria

1. THE Rank_Service SHALL use the locked rank table: Empty-Suit (0 XP, cap 50), Runner (1,000 XP, cap 75), Enforcer (5,000 XP, cap 100), Capo (25,000 XP, cap 150), Fixer (100,000 XP, cap 200), Underboss (500,000 XP, cap 250), Godfather (2,000,000 XP, cap 300).
2. WHEN a player's cumulative XP reaches or exceeds the threshold for the next Rank, THE Rank_Service SHALL promote the player to that Rank.
3. WHEN a player is promoted to a new Rank, THE Rank_Service SHALL update the player's Nerve cap via the Nerve_Service to reflect the new Rank's maximum.
4. THE Rank_Service SHALL store XP as a cumulative BigInteger value that only increases.
5. WHEN XP is awarded, THE Rank_Service SHALL check for multi-rank promotions (e.g., a single large XP award that skips intermediate ranks) and promote to the correct final Rank.
6. THE Rank_Service SHALL persist rank changes within the same database transaction as the XP award to prevent inconsistency.

### Requirement 9: Immutable Ledger and Wallet Integrity

**User Story:** As the game operator, I want all economy mutations to flow through an append-only ledger with idempotency, so that the economy is auditable and tamper-proof.

#### Acceptance Criteria

1. THE Ledger_Service SHALL process all CASH balance changes exclusively through append-only ledger entries using the RESERVE → CAPTURE → RELEASE model.
2. THE Ledger_Service SHALL enforce a database-level CHECK constraint that prevents any Wallet balance from going negative.
3. THE Ledger_Service SHALL enforce a database-level CHECK constraint that all ledger entry amounts are positive integers.
4. THE Ledger_Service SHALL enforce idempotency on every state-changing operation using the scoped idempotency_key (owner_type, owner_id, action, idempotency_key).
5. IF an idempotency_key is reused with a different request payload (different fingerprint), THEN THE Ledger_Service SHALL reject the request with an "idempotency_conflict" error.
6. THE Ledger_Service SHALL acquire a row-level lock (SELECT ... FOR UPDATE) on the target Wallet before any balance mutation to prevent race conditions.
7. THE Ledger_Service SHALL support an EARN entry type for crediting rewards (e.g., crime CASH payouts) that increases the Wallet balance and appends a POSTED ledger row.

### Requirement 10: Daily Reconciliation

**User Story:** As the game operator, I want a daily reconciliation job that verifies wallet balances against ledger entries, so that any discrepancy is detected immediately.

#### Acceptance Criteria

1. THE Reconciliation_Job SHALL run once per day on a configurable schedule.
2. WHEN the Reconciliation_Job executes, THE Reconciliation_Job SHALL compare each Wallet's balance to the sum of all POSTED ledger entries for that Wallet's (owner_type, owner_id, currency) tuple.
3. IF the Reconciliation_Job detects a mismatch between a Wallet balance and the ledger-derived balance, THEN THE Reconciliation_Job SHALL flag the discrepancy as a SEV-1 incident by emitting an alert to the configured alerting channel.
4. WHEN the Reconciliation_Job completes, THE Reconciliation_Job SHALL log a summary including the number of wallets checked, the number of mismatches found, and the total execution time.

### Requirement 11: Idempotency on All State-Changing Endpoints

**User Story:** As a developer, I want every state-changing API endpoint to require an idempotency key, so that network retries and duplicate requests are safe.

#### Acceptance Criteria

1. THE Backend SHALL require an idempotency_key header or field on every state-changing API request (POST, PUT, PATCH, DELETE).
2. IF a state-changing request is received without an idempotency_key, THEN THE Backend SHALL reject the request with a "missing_idempotency_key" error.
3. WHEN a duplicate request is received with the same idempotency_key and matching payload fingerprint, THE Backend SHALL return the cached response from the original request without re-executing the operation.

### Requirement 12: Configurable Game Constants

**User Story:** As the game operator, I want all economic and gameplay constants to be configurable without a code redeploy, so that the team can tune balance without engineering changes.

#### Acceptance Criteria

1. THE Backend SHALL load all game constants (Nerve regeneration rate, Nerve caps per rank, crime reward ranges, crime Nerve costs, crime Heat values, Heat decay rate, reconciliation schedule) from a configuration source external to application code.
2. WHEN a configuration value is changed, THE Backend SHALL apply the new value without requiring a restart or redeployment of the application.
