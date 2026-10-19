"""Tables owned by the mobile_card module."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Table,
    Text,
)

from app.db.metadata import public_metadata

mobile_card_april_state = Table(
    "mobile_card_april_state",
    public_metadata,
    Column("enabled", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column(
        "updated_by_user_account_id",
        BigInteger,
        ForeignKey("public.user_accounts.id", ondelete="SET NULL"),
    ),
)

mobile_card_access_codes = Table(
    "mobile_card_access_codes",
    public_metadata,
    Column("volunteer_id", BigInteger, ForeignKey("public.volunteer_records.id"), primary_key=True),
    Column("code_hash", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

mobile_card_trial_access_codes = Table(
    "mobile_card_trial_access_codes",
    public_metadata,
    Column(
        "application_id",
        BigInteger,
        ForeignKey("public.volunteer_application_invites.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("code_hash", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# Long-lived device credential that survives app-secret rotation and session
# expiry; exchanged for a fresh signed session token at POST /sessions/refresh.
mobile_card_refresh_tokens = Table(
    "mobile_card_refresh_tokens",
    public_metadata,
    Column("token_hash", Text, primary_key=True),
    Column(
        "volunteer_id",
        BigInteger,
        ForeignKey("public.volunteer_records.id", ondelete="CASCADE"),
    ),
    Column(
        "application_id",
        BigInteger,
        ForeignKey("public.volunteer_application_invites.id", ondelete="CASCADE"),
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "(volunteer_id IS NULL) <> (application_id IS NULL)",
        name="mobile_card_refresh_tokens_one_subject",
    ),
)
