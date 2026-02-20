# Requirements Document — AI MAFIA: Milestone 2 (Syndicate & Social)

## Introduction

Milestone 2 introduces the social layer of AI MAFIA. Players who reach Rank 4 (Capo, 25,000 XP) can create or join a Family (syndicate). Families have a four-tier role hierarchy (Don, Underboss, Capo, Soldier), a shared Vault funded by an automatic 10% tax on member CASH earnings, a real-time SSE chat channel, and a Property system that generates daily passive CASH income. All financial operations flow through the existing immutable Ledger Service. All new game constants are configurable without code redeploy via ConfigService.

## Glossary

- **Family**: A player-created syndicate group with a name, tag, and role hierarchy.
- **Family_Service**: The backend subsystem that manages Family creation, membership, role assignments, and dissolution.
- **Family_Vault_Service**: The backend subsystem that manages the Family's shared CASH Wallet, automatic tax collection, and Don-authorized withdrawals.
- **Chat_Service**: The backend subsystem that manages real-time SSE message delivery and message persistence for Family chat.
- **Property_Service**: The backend subsystem that manages Property ownership, purchase, upgrade, and daily passive income generation.
- **Income_Job**: A scheduled daily cron job that calculates and distributes passive CASH income from owned Properties to their owning Family's Vault.
- **Don**: The founding role of a Family; highest authority. One per Family.
- **Underboss**: Second-in-command role; can manage Capos and Soldiers. Maximum of 1 per Family.
- **Capo**: Mid-tier officer role; can invite and kick Soldiers. Maximum configurable per Family (default 3).
- **Soldier**: Base member role with no management permissions.
- **Family_Vault**: A CASH Wallet owned by the Family (owner_type=FAMILY) that accumulates tax revenue and Property income.
- **Property**: A purchasable in-game location (e.g., Speakeasy, Casino, Docks) that generates daily passive CASH income for the owning Family.
- **Vault_Tax_Rate**: The configurable percentage (default 10%) of CASH earnings automatically taxed from members into the Family Vault.
- **SSE**: Server-Sent Events; the transport protocol used for real-time chat delivery from server to client.
- **Redis_PubSub**: The Redis publish/subscribe mechanism used to fan out chat messages across server instances.

## Requirements

### Requirement 1: Family Creation

**User Story:** As a player who has reached Capo rank, I want to create a Family, so that I can lead a syndicate and recruit other players.

#### Acceptance Criteria

1. WHEN a player with Rank "Capo" or higher requests to create a Family with a valid name and tag, THE Family_Service SHALL create the Family record, assign the player as Don, and create a CASH Wallet for the Family Vault with a zero balance.
2. THE Family_Service SHALL validate that the Family name is between 3 and 24 characters, contains only alphanumeric characters, spaces, and underscores, and is unique across all Families.
3. THE Family_Service SHALL validate that the Family tag is between 2 and 5 uppercase alphanumeric characters and is unique across all Families.
4. IF a player with Rank below "Capo" attempts to create a Family, THEN THE Family_Service SHALL reject the request with a "rank_too_low" error.
5. IF a player who is already a member of a Family attempts to create a new Family, THEN THE Family_Service SHALL reject the request with an "already_in_family" error.
6. THE Family_Service SHALL enforce idempotency on the Family creation endpoint using the provided idempotency_key.

### Requirement 2: Family Membership (Join / Leave / Kick)

**User Story:** As a player, I want to join an existing Family, leave my current Family, or be removed by an officer, so that the syndicate roster stays dynamic.

#### Acceptance Criteria

1. WHEN a player with Rank "Capo" or higher requests to join a Family that has not reached its maximum member count, THE Family_Service SHALL add the player as a Soldier.
2. IF a player with Rank below "Capo" attempts to join a Family, THEN THE Family_Service SHALL reject the request with a "rank_too_low" error.
3. IF a player who is already a member of a Family attempts to join another Family, THEN THE Family_Service SHALL reject the request with an "already_in_family" error.
4. IF a player attempts to join a Family that has reached its maximum member count (configurable, default 25), THEN THE Family_Service SHALL reject the request with a "family_full" error.
5. WHEN a Soldier, Capo, or Underboss requests to leave a Family, THE Family_Service SHALL remove the player from the Family roster and revoke the player's role.
6. IF the Don attempts to leave the Family while other members remain, THEN THE Family_Service SHALL reject the request with a "don_must_transfer_or_disband" error.
7. WHEN a player with Capo role or higher kicks a Soldier from the Family, THE Family_Service SHALL remove the target Soldier from the roster.
8. WHEN a player with Underboss role or higher kicks a Capo from the Family, THE Family_Service SHALL remove the target Capo from the roster.
9. WHEN the Don kicks an Underboss from the Family, THE Family_Service SHALL remove the target Underboss from the roster.
10. IF a player attempts to kick a member of equal or higher role, THEN THE Family_Service SHALL reject the request with an "insufficient_permission" error.
11. THE Family_Service SHALL enforce idempotency on join, leave, and kick endpoints using the provided idempotency_key.

### Requirement 3: Role-Based Permissions

**User Story:** As a Don or Underboss, I want to promote and demote Family members, so that I can delegate authority within the syndicate.

#### Acceptance Criteria

1. WHEN the Don promotes a Soldier to Capo, THE Family_Service SHALL update the member's role to Capo, provided the Family has not reached the maximum Capo count (configurable, default 3).
2. WHEN the Don promotes a Capo to Underboss, THE Family_Service SHALL update the member's role to Underboss, provided no Underboss currently exists in the Family.
3. WHEN the Don demotes an Underboss to Capo, THE Family_Service SHALL update the member's role to Capo.
4. WHEN the Don or Underboss demotes a Capo to Soldier, THE Family_Service SHALL update the member's role to Soldier.
5. IF a promotion would exceed the maximum count for the target role, THEN THE Family_Service SHALL reject the request with a "role_limit_reached" error.
6. IF a player without sufficient role authority attempts a promotion or demotion, THEN THE Family_Service SHALL reject the request with an "insufficient_permission" error.
7. WHEN the Don transfers the Don role to another member, THE Family_Service SHALL assign the Don role to the target member and demote the former Don to Underboss (or Capo if Underboss is occupied).
8. THE Family_Service SHALL enforce idempotency on all role-change endpoints using the provided idempotency_key.

### Requirement 4: Family Dissolution

**User Story:** As a Don, I want to disband my Family when it is no longer active, so that the name and tag become available again.

#### Acceptance Criteria

1. WHEN the Don requests to disband the Family and the Family has no members other than the Don, THE Family_Service SHALL mark the Family as disbanded, release the Family name and tag for reuse, and remove the Don from the roster.
2. IF the Don attempts to disband the Family while other members remain, THEN THE Family_Service SHALL reject the request with a "family_has_members" error.
3. WHEN a Family is disbanded, THE Family_Service SHALL transfer any remaining Family Vault CASH balance to the Don's personal CASH Wallet via the Ledger_Service using a TRANSFER entry.
4. THE Family_Service SHALL enforce idempotency on the disband endpoint using the provided idempotency_key.

### Requirement 5: Family Vault and Automatic Tax

**User Story:** As a Family member, I want 10% of my CASH earnings to automatically go to the Family Vault, so that the syndicate accumulates shared wealth.

#### Acceptance Criteria

1. WHEN a Family member earns CASH from any source (crime payout, Property income distribution), THE Family_Vault_Service SHALL calculate the tax as floor(gross_amount * vault_tax_rate / 100) where vault_tax_rate is configurable (default 10).
2. WHEN the tax amount is greater than zero, THE Family_Vault_Service SHALL deduct the tax from the member's CASH earning and credit the tax amount to the Family Vault's CASH Wallet via the Ledger_Service using a TAX entry with the member's player ID as counterparty.
3. WHEN the tax amount is greater than zero, THE Family_Vault_Service SHALL credit the net amount (gross minus tax) to the member's personal CASH Wallet via the Ledger_Service using an EARN entry.
4. IF the calculated tax amount is zero (e.g., gross amount less than 10 at 10% rate), THEN THE Family_Vault_Service SHALL credit the full gross amount to the member's personal CASH Wallet with no tax deduction.
5. THE Family_Vault_Service SHALL process the tax deduction and member credit atomically within a single database transaction.
6. THE Family_Vault_Service SHALL enforce idempotency on each tax operation using the provided idempotency_key.

### Requirement 6: Vault Withdrawal

**User Story:** As a Don, I want to withdraw CASH from the Family Vault to distribute to members, so that shared funds can be used strategically.

#### Acceptance Criteria

1. WHEN the Don requests a withdrawal from the Family Vault specifying an amount and a target member, THE Family_Vault_Service SHALL transfer the specified CASH amount from the Family Vault Wallet to the target member's personal CASH Wallet via the Ledger_Service using a TRANSFER entry.
2. IF the requested withdrawal amount exceeds the Family Vault's available CASH balance, THEN THE Family_Vault_Service SHALL reject the request with an "insufficient_vault_funds" error.
3. IF a player who is not the Don attempts a Vault withdrawal, THEN THE Family_Vault_Service SHALL reject the request with an "insufficient_permission" error.
4. IF the target member is not a current member of the Family, THEN THE Family_Vault_Service SHALL reject the request with an "invalid_target_member" error.
5. THE Family_Vault_Service SHALL enforce idempotency on the withdrawal endpoint using the provided idempotency_key.

### Requirement 7: Family Chat via SSE

**User Story:** As a Family member, I want to send and receive real-time chat messages within my Family, so that we can coordinate and socialize.

#### Acceptance Criteria

1. WHEN a Family member opens the chat channel, THE Chat_Service SHALL establish an SSE connection that streams new messages for the member's Family in real time.
2. WHEN a Family member sends a chat message (1–500 characters), THE Chat_Service SHALL persist the message to the database with the sender's player ID, Family ID, display name, message body, and timestamp, then publish the message to the Family's Redis PubSub channel.
3. WHEN a message is published to a Family's Redis PubSub channel, THE Chat_Service SHALL deliver the message to all connected SSE clients subscribed to that Family's channel.
4. IF a Family member sends a message that is empty or exceeds 500 characters, THEN THE Chat_Service SHALL reject the message with a "invalid_message_length" error.
5. IF a player who is not a member of the Family attempts to connect to the Family's chat channel, THEN THE Chat_Service SHALL reject the connection with an "unauthorized" error.
6. WHEN a Family member reconnects after a disconnection, THE Chat_Service SHALL provide the most recent 50 messages (configurable) from the persisted message history so the client can backfill missed messages.
7. THE Chat_Service SHALL include a heartbeat event on the SSE connection every 30 seconds (configurable) to keep the connection alive and detect stale clients.

### Requirement 8: Property Purchase

**User Story:** As a Don, I want to purchase Properties for my Family, so that the syndicate earns daily passive income.

#### Acceptance Criteria

1. THE Property_Service SHALL define Properties as configurable definitions (loaded from ConfigService) each with a property_id, name, purchase_price (integer CASH), daily_income (integer CASH), and max_level.
2. WHEN the Don requests to purchase a Property for the Family, THE Property_Service SHALL deduct the purchase_price from the Family Vault's CASH Wallet via the Ledger_Service using a SPEND entry and create an ownership record linking the Property to the Family at level 1.
3. IF the Family Vault's available CASH balance is less than the Property's purchase_price, THEN THE Property_Service SHALL reject the request with an "insufficient_vault_funds" error.
4. IF a player who is not the Don attempts to purchase a Property, THEN THE Property_Service SHALL reject the request with an "insufficient_permission" error.
5. IF the Family already owns the specified Property, THEN THE Property_Service SHALL reject the request with an "already_owned" error.
6. THE Property_Service SHALL enforce idempotency on the purchase endpoint using the provided idempotency_key.

### Requirement 9: Property Upgrade

**User Story:** As a Don, I want to upgrade Family Properties to increase daily income, so that the syndicate grows wealthier over time.

#### Acceptance Criteria

1. WHEN the Don requests to upgrade a Family-owned Property, THE Property_Service SHALL deduct the upgrade cost from the Family Vault's CASH Wallet via the Ledger_Service using a SPEND entry and increment the Property's level by 1.
2. THE Property_Service SHALL calculate the upgrade cost as: purchase_price * current_level (configurable formula via ConfigService).
3. THE Property_Service SHALL calculate the daily income for a Property at a given level as: base_daily_income * level.
4. IF the Property is already at max_level, THEN THE Property_Service SHALL reject the request with a "max_level_reached" error.
5. IF the Family Vault's available CASH balance is less than the upgrade cost, THEN THE Property_Service SHALL reject the request with an "insufficient_vault_funds" error.
6. IF a player who is not the Don attempts to upgrade a Property, THEN THE Property_Service SHALL reject the request with an "insufficient_permission" error.
7. THE Property_Service SHALL enforce idempotency on the upgrade endpoint using the provided idempotency_key.

### Requirement 10: Daily Passive Income Generation

**User Story:** As a Family, I want our Properties to generate daily CASH income into the Family Vault, so that the syndicate earns passive revenue.

#### Acceptance Criteria

1. THE Income_Job SHALL run once per day on a configurable schedule (default "0 5 * * *").
2. WHEN the Income_Job executes, THE Income_Job SHALL iterate over all active Families with owned Properties and calculate the total daily income as the sum of (base_daily_income * level) for each owned Property.
3. WHEN the Income_Job calculates a positive total daily income for a Family, THE Income_Job SHALL credit the total amount to the Family Vault's CASH Wallet via the Ledger_Service using an EARN entry with a date-scoped idempotency_key to prevent duplicate payouts on retry.
4. WHEN the Income_Job completes, THE Income_Job SHALL log a summary including the number of Families processed, total CASH distributed, and execution time.
5. IF the Income_Job encounters an error processing a single Family, THEN THE Income_Job SHALL log the error for that Family and continue processing remaining Families without halting.

### Requirement 11: Rank Gate for Family and Property Features

**User Story:** As the game operator, I want Family and Property features gated behind Rank 4 (Capo), so that new players must progress before accessing social features.

#### Acceptance Criteria

1. THE Backend SHALL require a minimum Rank of "Capo" (Rank 4, 25,000 XP) to create or join a Family.
2. THE Backend SHALL require a minimum Rank of "Capo" (Rank 4, 25,000 XP) to purchase a Safehouse Property (the entry-level Property).
3. IF a player below Rank "Capo" attempts any Family or Property action, THEN THE Backend SHALL reject the request with a "rank_too_low" error indicating the required rank.

### Requirement 12: Configurable Milestone 2 Constants

**User Story:** As the game operator, I want all Milestone 2 gameplay constants to be configurable without a code redeploy, so that the team can tune syndicate balance dynamically.

#### Acceptance Criteria

1. THE Backend SHALL load the following constants from ConfigService: vault_tax_rate (default 10), max_family_members (default 25), max_capo_count (default 3), property_definitions (JSON array), income_job_schedule (default "0 5 * * *"), chat_history_limit (default 50), chat_heartbeat_interval (default 30).
2. WHEN a configuration value is changed in Redis, THE Backend SHALL apply the new value without requiring a restart or redeployment of the application.

### Requirement 13: Idempotency on All Milestone 2 State-Changing Endpoints

**User Story:** As a developer, I want every Milestone 2 state-changing endpoint to require an idempotency key, so that network retries and duplicate requests are safe.

#### Acceptance Criteria

1. THE Backend SHALL require an idempotency_key header or field on every Milestone 2 state-changing API request (Family create, join, leave, kick, promote, demote, transfer, disband, vault withdraw, property purchase, property upgrade, chat send).
2. IF a state-changing request is received without an idempotency_key, THEN THE Backend SHALL reject the request with a "missing_idempotency_key" error.
3. WHEN a duplicate request is received with the same idempotency_key and matching payload fingerprint, THE Backend SHALL return the cached response from the original request without re-executing the operation.
