"""
Unit tests for FamilyService — create, join, leave, kick, promote, demote,
transfer_don, disband, and query methods.

Validates: Requirements 1.1–1.6, 2.1–2.11, 3.1–3.8, 4.1–4.4, 11.1–11.3
"""

from __future__ import annotations

import uuid

import pytest

from services.api_fastapi.domain.models.economy import Currency, OwnerType, Wallet
from services.api_fastapi.domain.models.family import (
    Family,
    FamilyMember,
    FamilyRole,
    FamilyStatus,
)
from services.api_fastapi.domain.services.family_service import (
    AlreadyInFamily,
    DonMustTransferOrDisband,
    FamilyFull,
    FamilyHasMembers,
    FamilyNotFound,
    FamilyService,
    InvalidName,
    InvalidTag,
    NameTaken,
    NotInFamily,
    RankTooLow,
    RoleLimitReached,
    TagTaken,
)
from services.api_fastapi.domain.services.vault_service import InsufficientPermission


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_family_with_don(
    db_session, player_factory, redis_client, config_service,
    *, name="TestFam", tag="TF", rank="Capo",
):
    """Helper: create a player + family, return (service, player, family_result)."""
    player = await player_factory(rank=rank, display_name=f"P{uuid.uuid4().hex[:6]}")
    svc = FamilyService(redis_client, config_service)
    result = await svc.create_family(db_session, player.id, name, tag, f"idem-{uuid.uuid4().hex[:8]}")
    return svc, player, result


# ---------------------------------------------------------------------------
# Task 7.1 — create_family
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_family_success(db_session, player_factory, redis_client, config_service):
    """Capo+ player creates a family and becomes Don with zero-balance vault."""
    svc, player, result = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
    )
    assert result.name == "TestFam"
    assert result.tag == "TF"

    # Player is DON
    from sqlalchemy import select
    stmt = select(FamilyMember).where(FamilyMember.player_id == player.id)
    row = (await db_session.execute(stmt)).scalar_one()
    assert row.role == FamilyRole.DON

    # Family wallet exists with balance 0
    stmt = select(Wallet).where(
        Wallet.owner_type == OwnerType.FAMILY,
        Wallet.owner_id == result.family_id,
    )
    wallet = (await db_session.execute(stmt)).scalar_one()
    assert wallet.balance == 0


@pytest.mark.asyncio
async def test_create_family_rank_too_low(db_session, player_factory, redis_client, config_service):
    """Player below Capo cannot create a family."""
    player = await player_factory(rank="Runner", display_name="LowRank")
    svc = FamilyService(redis_client, config_service)
    with pytest.raises(RankTooLow):
        await svc.create_family(db_session, player.id, "Fam", "FA", "idem-1")


@pytest.mark.asyncio
async def test_create_family_already_in_family(db_session, player_factory, redis_client, config_service):
    """Player already in a family cannot create another."""
    svc, player, _ = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
    )
    with pytest.raises(AlreadyInFamily):
        await svc.create_family(db_session, player.id, "Fam2", "F2", "idem-2")


@pytest.mark.asyncio
async def test_create_family_invalid_name(db_session, player_factory, redis_client, config_service):
    """Invalid name is rejected."""
    player = await player_factory(rank="Capo", display_name="NameTest")
    svc = FamilyService(redis_client, config_service)
    with pytest.raises(InvalidName):
        await svc.create_family(db_session, player.id, "AB", "TG", "idem-3")  # too short


@pytest.mark.asyncio
async def test_create_family_invalid_tag(db_session, player_factory, redis_client, config_service):
    """Invalid tag is rejected."""
    player = await player_factory(rank="Capo", display_name="TagTest1")
    svc = FamilyService(redis_client, config_service)
    with pytest.raises(InvalidTag):
        await svc.create_family(db_session, player.id, "ValidName", "x", "idem-4")  # lowercase, too short


@pytest.mark.asyncio
async def test_create_family_duplicate_name(db_session, player_factory, redis_client, config_service):
    """Duplicate active family name is rejected."""
    await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="UniqueName", tag="UN",
    )
    player2 = await player_factory(rank="Capo", display_name="Dup001")
    svc = FamilyService(redis_client, config_service)
    with pytest.raises(NameTaken):
        await svc.create_family(db_session, player2.id, "UniqueName", "U2", "idem-5")


@pytest.mark.asyncio
async def test_create_family_duplicate_tag(db_session, player_factory, redis_client, config_service):
    """Duplicate active family tag is rejected."""
    await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="FamA", tag="DT",
    )
    player2 = await player_factory(rank="Capo", display_name="Dup002")
    svc = FamilyService(redis_client, config_service)
    with pytest.raises(TagTaken):
        await svc.create_family(db_session, player2.id, "FamB", "DT", "idem-6")


# ---------------------------------------------------------------------------
# Task 7.1 — join_family
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_join_family_success(db_session, player_factory, redis_client, config_service):
    """Capo+ player joins as Soldier."""
    svc, _, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="JoinFam", tag="JF",
    )
    joiner = await player_factory(rank="Capo", display_name="Joiner1")
    result = await svc.join_family(db_session, joiner.id, fam.family_id, "idem-j1")
    assert result.family_id == fam.family_id

    from sqlalchemy import select
    stmt = select(FamilyMember).where(FamilyMember.player_id == joiner.id)
    row = (await db_session.execute(stmt)).scalar_one()
    assert row.role == FamilyRole.SOLDIER


@pytest.mark.asyncio
async def test_join_family_rank_too_low(db_session, player_factory, redis_client, config_service):
    """Below-Capo player cannot join."""
    _, _, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="JoinFam2", tag="J2",
    )
    low = await player_factory(rank="Enforcer", display_name="LowJoin")
    svc = FamilyService(redis_client, config_service)
    with pytest.raises(RankTooLow):
        await svc.join_family(db_session, low.id, fam.family_id, "idem-j2")


@pytest.mark.asyncio
async def test_join_family_already_in_family(db_session, player_factory, redis_client, config_service):
    """Player already in a family cannot join another."""
    svc, player, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="JoinFam3", tag="J3",
    )
    # Create a second family
    p2 = await player_factory(rank="Capo", display_name="Don002")
    fam2 = await svc.create_family(db_session, p2.id, "OtherFam", "OF", "idem-j3a")

    with pytest.raises(AlreadyInFamily):
        await svc.join_family(db_session, player.id, fam2.family_id, "idem-j3b")


@pytest.mark.asyncio
async def test_join_family_full(db_session, player_factory, redis_client, config_service):
    """Cannot join a full family."""
    # Set max to 2 for testing
    await redis_client.set("config:MAX_FAMILY_MEMBERS", "2")
    svc, _, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="FullFam", tag="FF",
    )
    joiner1 = await player_factory(rank="Capo", display_name="Fill01")
    await svc.join_family(db_session, joiner1.id, fam.family_id, "idem-j4a")

    joiner2 = await player_factory(rank="Capo", display_name="Fill02")
    with pytest.raises(FamilyFull):
        await svc.join_family(db_session, joiner2.id, fam.family_id, "idem-j4b")


# ---------------------------------------------------------------------------
# Task 7.1 — leave_family
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leave_family_soldier(db_session, player_factory, redis_client, config_service):
    """Soldier can leave freely."""
    svc, _, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="LeaveFam", tag="LF",
    )
    soldier = await player_factory(rank="Capo", display_name="Leaver1")
    await svc.join_family(db_session, soldier.id, fam.family_id, "idem-l1a")
    await svc.leave_family(db_session, soldier.id, "idem-l1b")

    from sqlalchemy import select
    stmt = select(FamilyMember).where(FamilyMember.player_id == soldier.id)
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_leave_family_don_with_members_rejected(db_session, player_factory, redis_client, config_service):
    """Don cannot leave while other members remain."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="DonLeave", tag="DL",
    )
    soldier = await player_factory(rank="Capo", display_name="Stay01")
    await svc.join_family(db_session, soldier.id, fam.family_id, "idem-l2a")

    with pytest.raises(DonMustTransferOrDisband):
        await svc.leave_family(db_session, don.id, "idem-l2b")


@pytest.mark.asyncio
async def test_leave_family_not_in_family(db_session, player_factory, redis_client, config_service):
    """Player not in a family gets NotInFamily."""
    player = await player_factory(rank="Capo", display_name="NoFam01")
    svc = FamilyService(redis_client, config_service)
    with pytest.raises(NotInFamily):
        await svc.leave_family(db_session, player.id, "idem-l3")


# ---------------------------------------------------------------------------
# Task 7.1 — kick_member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kick_member_don_kicks_soldier(db_session, player_factory, redis_client, config_service):
    """Don can kick a Soldier."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="KickFam", tag="KF",
    )
    soldier = await player_factory(rank="Capo", display_name="Kicked1")
    await svc.join_family(db_session, soldier.id, fam.family_id, "idem-k1a")
    await svc.kick_member(db_session, don.id, soldier.id, "idem-k1b")

    from sqlalchemy import select
    stmt = select(FamilyMember).where(FamilyMember.player_id == soldier.id)
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_kick_member_equal_role_rejected(db_session, player_factory, redis_client, config_service):
    """Cannot kick a member of equal role."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="KickEq", tag="KE",
    )
    soldier1 = await player_factory(rank="Capo", display_name="Eq001")
    soldier2 = await player_factory(rank="Capo", display_name="Eq002")
    await svc.join_family(db_session, soldier1.id, fam.family_id, "idem-k2a")
    await svc.join_family(db_session, soldier2.id, fam.family_id, "idem-k2b")

    with pytest.raises(InsufficientPermission):
        await svc.kick_member(db_session, soldier1.id, soldier2.id, "idem-k2c")


# ---------------------------------------------------------------------------
# Task 7.2 — promote_member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_soldier_to_capo(db_session, player_factory, redis_client, config_service):
    """Don promotes Soldier → Capo."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="PromoFam", tag="PF",
    )
    soldier = await player_factory(rank="Capo", display_name="Promo01")
    await svc.join_family(db_session, soldier.id, fam.family_id, "idem-p1a")

    result = await svc.promote_member(
        db_session, don.id, soldier.id, FamilyRole.CAPO, "idem-p1b",
    )
    assert result.old_role == FamilyRole.SOLDIER
    assert result.new_role == FamilyRole.CAPO


@pytest.mark.asyncio
async def test_promote_capo_to_underboss(db_session, player_factory, redis_client, config_service):
    """Don promotes Capo → Underboss."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="PromoUB", tag="PU",
    )
    soldier = await player_factory(rank="Capo", display_name="Promo02")
    await svc.join_family(db_session, soldier.id, fam.family_id, "idem-p2a")
    await svc.promote_member(db_session, don.id, soldier.id, FamilyRole.CAPO, "idem-p2b")
    result = await svc.promote_member(
        db_session, don.id, soldier.id, FamilyRole.UNDERBOSS, "idem-p2c",
    )
    assert result.new_role == FamilyRole.UNDERBOSS


@pytest.mark.asyncio
async def test_promote_non_don_rejected(db_session, player_factory, redis_client, config_service):
    """Non-Don cannot promote."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="PromoND", tag="PN",
    )
    s1 = await player_factory(rank="Capo", display_name="NonDon1")
    s2 = await player_factory(rank="Capo", display_name="NonDon2")
    await svc.join_family(db_session, s1.id, fam.family_id, "idem-p3a")
    await svc.join_family(db_session, s2.id, fam.family_id, "idem-p3b")

    with pytest.raises(InsufficientPermission):
        await svc.promote_member(db_session, s1.id, s2.id, FamilyRole.CAPO, "idem-p3c")


@pytest.mark.asyncio
async def test_promote_capo_limit_reached(db_session, player_factory, redis_client, config_service):
    """Cannot promote beyond max capo count."""
    await redis_client.set("config:MAX_CAPO_COUNT", "1")
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="CapoLim", tag="CL",
    )
    s1 = await player_factory(rank="Capo", display_name="CapoL1")
    s2 = await player_factory(rank="Capo", display_name="CapoL2")
    await svc.join_family(db_session, s1.id, fam.family_id, "idem-p4a")
    await svc.join_family(db_session, s2.id, fam.family_id, "idem-p4b")

    await svc.promote_member(db_session, don.id, s1.id, FamilyRole.CAPO, "idem-p4c")

    with pytest.raises(RoleLimitReached):
        await svc.promote_member(db_session, don.id, s2.id, FamilyRole.CAPO, "idem-p4d")


@pytest.mark.asyncio
async def test_promote_underboss_already_exists(db_session, player_factory, redis_client, config_service):
    """Cannot promote to Underboss when one already exists."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="UBExist", tag="UE",
    )
    s1 = await player_factory(rank="Capo", display_name="UBEx01")
    s2 = await player_factory(rank="Capo", display_name="UBEx02")
    await svc.join_family(db_session, s1.id, fam.family_id, "idem-p5a")
    await svc.join_family(db_session, s2.id, fam.family_id, "idem-p5b")

    await svc.promote_member(db_session, don.id, s1.id, FamilyRole.CAPO, "idem-p5c")
    await svc.promote_member(db_session, don.id, s1.id, FamilyRole.UNDERBOSS, "idem-p5d")

    await svc.promote_member(db_session, don.id, s2.id, FamilyRole.CAPO, "idem-p5e")
    with pytest.raises(RoleLimitReached):
        await svc.promote_member(db_session, don.id, s2.id, FamilyRole.UNDERBOSS, "idem-p5f")


# ---------------------------------------------------------------------------
# Task 7.2 — demote_member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demote_capo_to_soldier(db_session, player_factory, redis_client, config_service):
    """Don demotes Capo → Soldier."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="DemoFam", tag="DF",
    )
    s1 = await player_factory(rank="Capo", display_name="Demo01")
    await svc.join_family(db_session, s1.id, fam.family_id, "idem-d1a")
    await svc.promote_member(db_session, don.id, s1.id, FamilyRole.CAPO, "idem-d1b")

    result = await svc.demote_member(
        db_session, don.id, s1.id, FamilyRole.SOLDIER, "idem-d1c",
    )
    assert result.old_role == FamilyRole.CAPO
    assert result.new_role == FamilyRole.SOLDIER


@pytest.mark.asyncio
async def test_demote_underboss_to_capo(db_session, player_factory, redis_client, config_service):
    """Don demotes Underboss → Capo."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="DemoUB", tag="DU",
    )
    s1 = await player_factory(rank="Capo", display_name="Demo02")
    await svc.join_family(db_session, s1.id, fam.family_id, "idem-d2a")
    await svc.promote_member(db_session, don.id, s1.id, FamilyRole.CAPO, "idem-d2b")
    await svc.promote_member(db_session, don.id, s1.id, FamilyRole.UNDERBOSS, "idem-d2c")

    result = await svc.demote_member(
        db_session, don.id, s1.id, FamilyRole.CAPO, "idem-d2d",
    )
    assert result.old_role == FamilyRole.UNDERBOSS
    assert result.new_role == FamilyRole.CAPO


@pytest.mark.asyncio
async def test_demote_equal_role_rejected(db_session, player_factory, redis_client, config_service):
    """Cannot demote a member of equal or higher role."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="DemoEq", tag="DE",
    )
    s1 = await player_factory(rank="Capo", display_name="DemoE1")
    s2 = await player_factory(rank="Capo", display_name="DemoE2")
    await svc.join_family(db_session, s1.id, fam.family_id, "idem-d3a")
    await svc.join_family(db_session, s2.id, fam.family_id, "idem-d3b")

    # Soldiers can't demote each other
    with pytest.raises(InsufficientPermission):
        await svc.demote_member(db_session, s1.id, s2.id, FamilyRole.SOLDIER, "idem-d3c")


# ---------------------------------------------------------------------------
# Task 7.2 — transfer_don
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_don_to_soldier(db_session, player_factory, redis_client, config_service):
    """Don transfers to a Soldier; former Don becomes Underboss."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="XferFam", tag="XF",
    )
    soldier = await player_factory(rank="Capo", display_name="Xfer01")
    await svc.join_family(db_session, soldier.id, fam.family_id, "idem-t1a")

    result = await svc.transfer_don(db_session, don.id, soldier.id, "idem-t1b")
    assert result.new_role == FamilyRole.DON

    from sqlalchemy import select
    # Former Don is now Underboss
    stmt = select(FamilyMember).where(FamilyMember.player_id == don.id)
    row = (await db_session.execute(stmt)).scalar_one()
    assert row.role == FamilyRole.UNDERBOSS

    # Target is now Don
    stmt = select(FamilyMember).where(FamilyMember.player_id == soldier.id)
    row = (await db_session.execute(stmt)).scalar_one()
    assert row.role == FamilyRole.DON


@pytest.mark.asyncio
async def test_transfer_don_underboss_occupied(db_session, player_factory, redis_client, config_service):
    """When Underboss slot is occupied by another member, former Don becomes Capo."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="XferUB", tag="XU",
    )
    ub = await player_factory(rank="Capo", display_name="UBOcc01")
    target = await player_factory(rank="Capo", display_name="XferT1")
    await svc.join_family(db_session, ub.id, fam.family_id, "idem-t2a")
    await svc.join_family(db_session, target.id, fam.family_id, "idem-t2b")

    # Promote ub to Underboss
    await svc.promote_member(db_session, don.id, ub.id, FamilyRole.CAPO, "idem-t2c")
    await svc.promote_member(db_session, don.id, ub.id, FamilyRole.UNDERBOSS, "idem-t2d")

    # Transfer Don to target (a Soldier)
    await svc.transfer_don(db_session, don.id, target.id, "idem-t2e")

    from sqlalchemy import select
    # Former Don should be Capo (Underboss slot occupied by ub)
    stmt = select(FamilyMember).where(FamilyMember.player_id == don.id)
    row = (await db_session.execute(stmt)).scalar_one()
    assert row.role == FamilyRole.CAPO


@pytest.mark.asyncio
async def test_transfer_don_non_don_rejected(db_session, player_factory, redis_client, config_service):
    """Non-Don cannot transfer leadership."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="XferND", tag="XN",
    )
    s1 = await player_factory(rank="Capo", display_name="XferN1")
    s2 = await player_factory(rank="Capo", display_name="XferN2")
    await svc.join_family(db_session, s1.id, fam.family_id, "idem-t3a")
    await svc.join_family(db_session, s2.id, fam.family_id, "idem-t3b")

    with pytest.raises(InsufficientPermission):
        await svc.transfer_don(db_session, s1.id, s2.id, "idem-t3c")


# ---------------------------------------------------------------------------
# Task 7.3 — disband_family
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disband_family_sole_don(db_session, player_factory, redis_client, config_service, wallet_factory):
    """Don can disband when they are the only member."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="DisFam", tag="DS",
    )
    # Ensure Don has a player wallet for the transfer target
    await wallet_factory(owner_id=don.id, owner_type=OwnerType.PLAYER, currency=Currency.CASH)

    result = await svc.disband_family(db_session, don.id, "idem-dis1")
    assert result.family_id == fam.family_id
    assert result.vault_transferred == 0  # vault was empty

    # Family is DISBANDED
    family = await db_session.get(Family, fam.family_id)
    assert family.status == FamilyStatus.DISBANDED
    assert family.disbanded_at is not None

    # Don removed from roster
    from sqlalchemy import select
    stmt = select(FamilyMember).where(FamilyMember.player_id == don.id)
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_disband_family_with_members_rejected(db_session, player_factory, redis_client, config_service):
    """Cannot disband while other members remain."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="DisRej", tag="DR",
    )
    soldier = await player_factory(rank="Capo", display_name="DisR01")
    await svc.join_family(db_session, soldier.id, fam.family_id, "idem-dis2a")

    with pytest.raises(FamilyHasMembers):
        await svc.disband_family(db_session, don.id, "idem-dis2b")


@pytest.mark.asyncio
async def test_disband_family_non_don_rejected(db_session, player_factory, redis_client, config_service):
    """Non-Don cannot disband."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="DisND", tag="DN",
    )
    soldier = await player_factory(rank="Capo", display_name="DisND1")
    await svc.join_family(db_session, soldier.id, fam.family_id, "idem-dis3a")

    with pytest.raises(InsufficientPermission):
        await svc.disband_family(db_session, soldier.id, "idem-dis3b")


@pytest.mark.asyncio
async def test_disband_family_transfers_vault_balance(db_session, player_factory, redis_client, config_service, wallet_factory):
    """Disband transfers vault balance to Don."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="DisVault", tag="DV",
    )
    # Give the vault some balance
    from sqlalchemy import select
    stmt = select(Wallet).where(
        Wallet.owner_type == OwnerType.FAMILY,
        Wallet.owner_id == fam.family_id,
        Wallet.currency == Currency.CASH,
    )
    vault = (await db_session.execute(stmt)).scalar_one()
    vault.balance = 5000
    await db_session.flush()

    # Ensure Don has a player wallet
    await wallet_factory(owner_id=don.id, owner_type=OwnerType.PLAYER, currency=Currency.CASH)

    result = await svc.disband_family(db_session, don.id, "idem-dis4")
    assert result.vault_transferred == 5000


# ---------------------------------------------------------------------------
# Task 7.3 — Queries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_family(db_session, player_factory, redis_client, config_service):
    """get_family returns FamilyDetail."""
    svc, _, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="GetFam", tag="GF",
    )
    detail = await svc.get_family(db_session, fam.family_id)
    assert detail is not None
    assert detail.name == "GetFam"
    assert detail.member_count == 1


@pytest.mark.asyncio
async def test_get_family_not_found(db_session, redis_client, config_service):
    """get_family returns None for unknown ID."""
    svc = FamilyService(redis_client, config_service)
    detail = await svc.get_family(db_session, uuid.uuid4())
    assert detail is None


@pytest.mark.asyncio
async def test_get_player_family(db_session, player_factory, redis_client, config_service):
    """get_player_family returns the player's family."""
    svc, player, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="PlrFam", tag="PL",
    )
    detail = await svc.get_player_family(db_session, player.id)
    assert detail is not None
    assert detail.family_id == fam.family_id


@pytest.mark.asyncio
async def test_get_player_family_none(db_session, player_factory, redis_client, config_service):
    """get_player_family returns None when player has no family."""
    player = await player_factory(rank="Capo", display_name="NoFam02")
    svc = FamilyService(redis_client, config_service)
    detail = await svc.get_player_family(db_session, player.id)
    assert detail is None


@pytest.mark.asyncio
async def test_list_members(db_session, player_factory, redis_client, config_service):
    """list_members returns all members with display names."""
    svc, don, fam = await _create_family_with_don(
        db_session, player_factory, redis_client, config_service,
        name="ListFam", tag="LM",
    )
    s1 = await player_factory(rank="Capo", display_name="List01")
    await svc.join_family(db_session, s1.id, fam.family_id, "idem-lm1")

    members = await svc.list_members(db_session, fam.family_id)
    assert len(members) == 2
    roles = {m.role for m in members}
    assert FamilyRole.DON in roles
    assert FamilyRole.SOLDIER in roles
