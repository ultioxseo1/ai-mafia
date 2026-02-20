"""Initial schema: players, wallets, ledger_entries, idempotency_keys

Revision ID: 5ac43e966127
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "5ac43e966127"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Enum types ---
    owner_type = postgresql.ENUM("PLAYER", "FAMILY", "SYSTEM", name="owner_type", create_type=False)
    currency = postgresql.ENUM("CASH", "DIAMOND", "BULLET", name="currency", create_type=False)
    ledger_entry_type = postgresql.ENUM(
        "RESERVE", "CAPTURE", "RELEASE", "EARN", "SPEND", "TAX", "TRANSFER",
        name="ledger_entry_type", create_type=False,
    )
    ledger_entry_status = postgresql.ENUM("PENDING", "POSTED", "VOID", name="ledger_entry_status", create_type=False)

    owner_type.create(op.get_bind(), checkfirst=True)
    currency.create(op.get_bind(), checkfirst=True)
    ledger_entry_type.create(op.get_bind(), checkfirst=True)
    ledger_entry_status.create(op.get_bind(), checkfirst=True)

    # --- players ---
    op.create_table(
        "players",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("apple_sub", sa.String(256), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("display_name", sa.String(20), nullable=True),
        sa.Column("rank", sa.String(32), nullable=False, server_default="Empty-Suit"),
        sa.Column("xp", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("age_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("apple_sub", name="uq_players_apple_sub"),
        sa.UniqueConstraint("email", name="uq_players_email"),
        sa.UniqueConstraint("display_name", name="uq_players_display_name"),
        sa.CheckConstraint("xp >= 0", name="ck_player_xp_nonneg"),
        sa.CheckConstraint(
            "char_length(display_name) >= 3 AND char_length(display_name) <= 20",
            name="ck_player_name_length",
        ),
    )

    # --- wallets ---
    op.create_table(
        "wallets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_type", owner_type, nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", currency, nullable=False),
        sa.Column("balance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_balance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_type", "owner_id", "currency", name="uq_wallet_owner_currency"),
        sa.CheckConstraint("balance >= 0", name="ck_wallet_balance_nonneg"),
        sa.CheckConstraint("reserved_balance >= 0", name="ck_wallet_reserved_nonneg"),
    )
    op.create_index("ix_wallet_owner", "wallets", ["owner_type", "owner_id"])

    # --- idempotency_keys ---
    op.create_table(
        "idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_type", postgresql.ENUM("PLAYER", "FAMILY", "SYSTEM", name="idempo_owner_type", create_type=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(128), nullable=False),
        sa.Column("response_body", postgresql.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_type", "owner_id", "action", "idempotency_key", name="uq_idempo_scope_key"),
    )
    op.create_index("ix_idempo_lookup", "idempotency_keys", ["owner_type", "owner_id", "action", "idempotency_key"])

    # --- ledger_entries ---
    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_type", postgresql.ENUM("PLAYER", "FAMILY", "SYSTEM", name="ledger_owner_type", create_type=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", postgresql.ENUM("CASH", "DIAMOND", "BULLET", name="ledger_currency", create_type=True), nullable=False),
        sa.Column("entry_type", ledger_entry_type, nullable=False),
        sa.Column("status", ledger_entry_status, nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("reference_id", sa.String(64), nullable=False),
        sa.Column("metadata", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount > 0", name="ck_ledger_amount_positive"),
    )
    op.create_index("ix_ledger_owner_currency_time", "ledger_entries", ["owner_type", "owner_id", "currency", "created_at"])
    op.create_index("ix_ledger_reference", "ledger_entries", ["reference_id"])


def downgrade() -> None:
    op.drop_table("ledger_entries")
    op.drop_table("idempotency_keys")
    op.drop_table("wallets")
    op.drop_table("players")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS ledger_currency")
    op.execute("DROP TYPE IF EXISTS ledger_owner_type")
    op.execute("DROP TYPE IF EXISTS idempo_owner_type")
    op.execute("DROP TYPE IF EXISTS ledger_entry_status")
    op.execute("DROP TYPE IF EXISTS ledger_entry_type")
    op.execute("DROP TYPE IF EXISTS currency")
    op.execute("DROP TYPE IF EXISTS owner_type")
