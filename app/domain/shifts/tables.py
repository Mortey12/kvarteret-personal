"""Tables owned by the shifts module (Skiftlista).

Templates describe the recurring week ("Dagskift in Stjernesalen every
weekday 11:30-17:00 with one skiftleder and one skjenker"). Shifts are
the concrete dated instances generated from them, and can be edited one
by one. Slots are the places on a shift; assignments put a volunteer in
a place.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Table,
    Text,
    Time,
    UniqueConstraint,
    func,
)

from app.db.metadata import public_metadata

shift_templates = Table(
    "shift_templates",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column("group_id", BigInteger, ForeignKey("public.groups.id"), nullable=False),
    Column("venue", Text, nullable=False),
    Column("title", Text, nullable=False),
    # 0 = Monday ... 6 = Sunday, like date.weekday().
    Column("weekday", SmallInteger, nullable=False),
    Column("start_time", Time, nullable=False),
    # An end time earlier than the start time means the shift ends after midnight.
    Column("end_time", Time, nullable=False),
    # True: an opplæring place takes one of the regular places (little room in the bar).
    Column("training_replaces_slot", Boolean, nullable=False),
    Column("is_active", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

shift_template_slots = Table(
    "shift_template_slots",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "template_id",
        BigInteger,
        ForeignKey("public.shift_templates.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", Text, nullable=False),
    Column("count", Integer, nullable=False),
    Column("open_for_samf", Boolean, nullable=False),
    Column("sort_order", Integer, nullable=False),
)

shifts = Table(
    "shifts",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column("group_id", BigInteger, ForeignKey("public.groups.id"), nullable=False),
    Column(
        "template_id",
        BigInteger,
        ForeignKey("public.shift_templates.id", ondelete="SET NULL"),
    ),
    Column("venue", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("starts_at", DateTime(timezone=True), nullable=False),
    Column("ends_at", DateTime(timezone=True), nullable=False),
    Column("note", Text),
    Column("is_closed", Boolean, nullable=False),
    Column("training_replaces_slot", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

shift_slots = Table(
    "shift_slots",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "shift_id",
        BigInteger,
        ForeignKey("public.shifts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", Text, nullable=False),
    Column("count", Integer, nullable=False),
    Column("open_for_samf", Boolean, nullable=False),
    Column("sort_order", Integer, nullable=False),
)

shift_assignments = Table(
    "shift_assignments",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "slot_id",
        BigInteger,
        ForeignKey("public.shift_slots.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "volunteer_id",
        BigInteger,
        ForeignKey("public.volunteer_records.id"),
        nullable=False,
    ),
    Column("swap_requested", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("slot_id", "volunteer_id", name="shift_assignments_slot_volunteer_key"),
)
