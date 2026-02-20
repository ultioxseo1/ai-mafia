"""Milestone 2: families, family_members, family_properties, chat_messages,
ledger_entries counterparty columns

Revision ID: a1b2c3d4e5f6
Revises: 5ac43e966127
Create Date: 2025-01-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "5ac43e966127"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Enum types ---
    family_status = postgresql.ENUM("ACTIVE", "DISBANDED", name="family_status", create_type=False)
    family_role = postgresql.ENUM("SOLDIER", "CAPO", "UNDERBOSS", "DON", name="family_role", create_type=False)
    # Reuse existing owner_type enum for counterparty columns
    ledger_counterparty_owner_type = postgresql.ENUM(
        "PLAYER", "FAMILY", "SYSTEM", name="ledger_counterparty_owner_type", create_type=False,
    )

    family_status.create(op.get_bind(), checkfirst=True)
    family_role.create(op.get_bind(), checkfirst=True)
    ledger_counterparty_owner_type.create(op.get_bind(), checkfirst=True)

    # --- Add counterparty columns to ledger_entries ---
    op.add_column(
        "ledger_entries",
        sa.Column("counterparty_owner_type", ledger_counterparty_owner_type, nullable=True),
    )
    op.add_column(
        "ledger_entries",
        sa.Column("counterparty_owner_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # --- families ---
    op.create_table(
        "families",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(24), nullable=False),
        sa.Column("tag", sa.String(5), nullable=False),
        sa.Column("status", family_status, nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("disbanded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(name) >= 3 AND char_length(name) <= 24",
            name="ck_family_name_length",
        ),
        sa.CheckConstraint(
            "char_length(tag) >= 2 AND char_length(tag) <= 5",
            name="ck_family_tag_length",
        ),
    )
    # Partial unique indexes: only enforce uniqueness among ACTIVE families,
    # allowing disbanded families to release their name/tag for reuse.
    op.create_index(
        "ix_family_name_active",
        "families",
        ["name"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_family_tag_active",
        "families",
        ["tag"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    # --- family_members ---
    op.create_table(
        "family_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("families.id"), nullable=False),
        sa.Column("player_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("role", family_role, nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("player_id", name="uq_family_member_player"),
    )
    op.create_index("ix_family_member_family", "family_members", ["family_id"])

    # --- family_properties ---
    op.create_table(
        "family_properties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("families.id"), nullable=False),
        sa.Column("property_id", sa.String(64), nullable=False),
        sa.Column("level", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("family_id", "property_id", name="uq_family_property"),
        sa.CheckConstraint("level >= 1", name="ck_property_level_min"),
    )

    # --- chat_messages ---
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("families.id"), nullable=False),
        sa.Column("player_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("display_name", sa.String(20), nullable=False),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "char_length(body) >= 1 AND char_length(body) <= 500",
            name="ck_chat_body_length",
        ),
    )
    op.create_index("ix_chat_family_time", "chat_messages", ["family_id", "created_at"])


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("family_properties")
    op.drop_table("family_members")
    op.drop_index("ix_family_tag_active", table_name="families")
    op.drop_index("ix_family_name_active", table_name="families")
    op.drop_table("families")

    op.drop_column("ledger_entries", "counterparty_owner_id")
    op.drop_column("ledger_entries", "counterparty_owner_type")

    # Drop enum types created in this migration
    op.execute("DROP TYPE IF EXISTS ledger_counterparty_owner_type")
    op.execute("DROP TYPE IF EXISTS family_role")
    op.execute("DROP TYPE IF EXISTS family_status")
