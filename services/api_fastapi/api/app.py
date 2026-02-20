"""
services/api_fastapi/api/app.py

FastAPI application entrypoint.

- Registers IdempotencyMiddleware
- Includes auth, profile, crime, nerve routers
- Configures error handlers for the full error catalog
- JWT auth and age-gate are applied via per-router dependencies

Requirements: 3.1, 3.2, 11.1
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from services.api_fastapi.api.routers.auth_router import router as auth_router
from services.api_fastapi.api.routers.chat_router import router as chat_router
from services.api_fastapi.api.routers.crime_router import router as crime_router
from services.api_fastapi.api.routers.family_router import router as family_router
from services.api_fastapi.api.routers.nerve_router import router as nerve_router
from services.api_fastapi.api.routers.profile_router import router as profile_router
from services.api_fastapi.api.routers.property_router import (
    family_properties_router,
    properties_router,
)
from services.api_fastapi.api.routers.vault_router import router as vault_router
from services.api_fastapi.domain.services.auth_service import (
    AgeRequired,
    InvalidOTP,
    InvalidToken,
    OTPExpired,
    RateLimited,
    UpstreamUnavailable,
)
from services.api_fastapi.domain.services.chat_service import InvalidMessageLength
from services.api_fastapi.domain.services.crime_service import CrimeNotFound
from services.api_fastapi.domain.services.family_service import (
    AlreadyInFamily,
    DonMustTransferOrDisband,
    FamilyFull,
    FamilyHasMembers,
    FamilyNotFound,
    InvalidName as FamilyInvalidName,
    InvalidTag,
    NameTaken as FamilyNameTaken,
    NotInFamily,
    RankTooLow,
    RoleLimitReached,
    TagTaken,
)
from services.api_fastapi.domain.services.ledger_service import (
    IdempotencyConflict,
    InsufficientFunds,
)
from services.api_fastapi.domain.services.nerve_service import InsufficientNerve
from services.api_fastapi.domain.services.profile_service import (
    InvalidName,
    NameTaken,
)
from services.api_fastapi.domain.services.property_service import (
    AlreadyOwned,
    MaxLevelReached,
    PropertyNotFound,
)
from services.api_fastapi.domain.services.vault_service import (
    InsufficientPermission,
    InsufficientVaultFunds,
    InvalidTargetMember,
)
from services.api_fastapi.middleware.idempotency import IdempotencyMiddleware


# ---------------------------------------------------------------------------
# Error response helper
# ---------------------------------------------------------------------------

def _error_response(status: int, code: str, message: str, retriable: bool = False) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": str(message), "retriable": retriable}},
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(title="AI MAFIA", version="0.1.0")

    # -- Middleware (outermost first) ---------------------------------------
    app.add_middleware(IdempotencyMiddleware)

    # -- Routers ------------------------------------------------------------
    app.include_router(auth_router)
    app.include_router(profile_router)
    app.include_router(crime_router)
    app.include_router(nerve_router)
    app.include_router(family_router)
    app.include_router(vault_router)
    app.include_router(properties_router)
    app.include_router(family_properties_router)
    app.include_router(chat_router)

    # -- Error handlers -----------------------------------------------------

    @app.exception_handler(InvalidToken)
    async def _invalid_token(_req: Request, exc: InvalidToken) -> JSONResponse:
        return _error_response(401, "invalid_token", exc)

    @app.exception_handler(UpstreamUnavailable)
    async def _upstream(_req: Request, exc: UpstreamUnavailable) -> JSONResponse:
        return _error_response(503, "upstream_unavailable", exc, retriable=True)

    @app.exception_handler(InvalidOTP)
    async def _invalid_otp(_req: Request, exc: InvalidOTP) -> JSONResponse:
        return _error_response(401, "invalid_otp", exc)

    @app.exception_handler(OTPExpired)
    async def _otp_expired(_req: Request, exc: OTPExpired) -> JSONResponse:
        return _error_response(401, "otp_expired", exc)

    @app.exception_handler(RateLimited)
    async def _rate_limited(_req: Request, exc: RateLimited) -> JSONResponse:
        return _error_response(429, "rate_limited", exc, retriable=True)

    @app.exception_handler(AgeRequired)
    async def _age_required(_req: Request, exc: AgeRequired) -> JSONResponse:
        return _error_response(403, "age_required", exc)

    @app.exception_handler(NameTaken)
    async def _name_taken(_req: Request, exc: NameTaken) -> JSONResponse:
        return _error_response(409, "name_taken", exc)

    @app.exception_handler(InvalidName)
    async def _invalid_name(_req: Request, exc: InvalidName) -> JSONResponse:
        return _error_response(422, "invalid_name", exc)

    @app.exception_handler(InsufficientNerve)
    async def _insufficient_nerve(_req: Request, exc: InsufficientNerve) -> JSONResponse:
        return _error_response(409, "insufficient_nerve", exc)

    @app.exception_handler(InsufficientFunds)
    async def _insufficient_funds(_req: Request, exc: InsufficientFunds) -> JSONResponse:
        return _error_response(409, "insufficient_funds", exc)

    @app.exception_handler(IdempotencyConflict)
    async def _idempotency_conflict(_req: Request, exc: IdempotencyConflict) -> JSONResponse:
        return _error_response(409, "idempotency_conflict", exc)

    @app.exception_handler(CrimeNotFound)
    async def _crime_not_found(_req: Request, exc: CrimeNotFound) -> JSONResponse:
        return _error_response(404, "crime_not_found", exc)

    # -- M2: Family error handlers ------------------------------------------

    @app.exception_handler(RankTooLow)
    async def _rank_too_low(_req: Request, exc: RankTooLow) -> JSONResponse:
        return _error_response(403, "rank_too_low", exc)

    @app.exception_handler(AlreadyInFamily)
    async def _already_in_family(_req: Request, exc: AlreadyInFamily) -> JSONResponse:
        return _error_response(409, "already_in_family", exc)

    @app.exception_handler(FamilyFull)
    async def _family_full(_req: Request, exc: FamilyFull) -> JSONResponse:
        return _error_response(409, "family_full", exc)

    @app.exception_handler(DonMustTransferOrDisband)
    async def _don_must_transfer(_req: Request, exc: DonMustTransferOrDisband) -> JSONResponse:
        return _error_response(409, "don_must_transfer_or_disband", exc)

    @app.exception_handler(RoleLimitReached)
    async def _role_limit_reached(_req: Request, exc: RoleLimitReached) -> JSONResponse:
        return _error_response(409, "role_limit_reached", exc)

    @app.exception_handler(FamilyHasMembers)
    async def _family_has_members(_req: Request, exc: FamilyHasMembers) -> JSONResponse:
        return _error_response(409, "family_has_members", exc)

    @app.exception_handler(FamilyNotFound)
    async def _family_not_found(_req: Request, exc: FamilyNotFound) -> JSONResponse:
        return _error_response(404, "family_not_found", exc)

    @app.exception_handler(NotInFamily)
    async def _not_in_family(_req: Request, exc: NotInFamily) -> JSONResponse:
        return _error_response(403, "not_in_family", exc)

    @app.exception_handler(FamilyInvalidName)
    async def _family_invalid_name(_req: Request, exc: FamilyInvalidName) -> JSONResponse:
        return _error_response(422, "invalid_family_name", exc)

    @app.exception_handler(InvalidTag)
    async def _invalid_tag(_req: Request, exc: InvalidTag) -> JSONResponse:
        return _error_response(422, "invalid_tag", exc)

    @app.exception_handler(FamilyNameTaken)
    async def _family_name_taken(_req: Request, exc: FamilyNameTaken) -> JSONResponse:
        return _error_response(409, "family_name_taken", exc)

    @app.exception_handler(TagTaken)
    async def _tag_taken(_req: Request, exc: TagTaken) -> JSONResponse:
        return _error_response(409, "tag_taken", exc)

    # -- M2: Vault error handlers -------------------------------------------

    @app.exception_handler(InsufficientPermission)
    async def _insufficient_permission(_req: Request, exc: InsufficientPermission) -> JSONResponse:
        return _error_response(403, "insufficient_permission", exc)

    @app.exception_handler(InsufficientVaultFunds)
    async def _insufficient_vault_funds(_req: Request, exc: InsufficientVaultFunds) -> JSONResponse:
        return _error_response(409, "insufficient_vault_funds", exc)

    @app.exception_handler(InvalidTargetMember)
    async def _invalid_target_member(_req: Request, exc: InvalidTargetMember) -> JSONResponse:
        return _error_response(422, "invalid_target_member", exc)

    # -- M2: Chat error handlers --------------------------------------------

    @app.exception_handler(InvalidMessageLength)
    async def _invalid_message_length(_req: Request, exc: InvalidMessageLength) -> JSONResponse:
        return _error_response(422, "invalid_message_length", exc)

    # -- M2: Property error handlers ----------------------------------------

    @app.exception_handler(AlreadyOwned)
    async def _already_owned(_req: Request, exc: AlreadyOwned) -> JSONResponse:
        return _error_response(409, "already_owned", exc)

    @app.exception_handler(MaxLevelReached)
    async def _max_level_reached(_req: Request, exc: MaxLevelReached) -> JSONResponse:
        return _error_response(409, "max_level_reached", exc)

    @app.exception_handler(PropertyNotFound)
    async def _property_not_found(_req: Request, exc: PropertyNotFound) -> JSONResponse:
        return _error_response(404, "property_not_found", exc)

    # -- Generic fallback ---------------------------------------------------

    @app.exception_handler(Exception)
    async def _internal_error(_req: Request, exc: Exception) -> JSONResponse:
        return _error_response(500, "internal_error", "An unexpected error occurred.", retriable=True)

    return app
