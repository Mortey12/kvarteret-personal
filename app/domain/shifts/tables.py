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
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Table,
    Text,
    Time,
    UniqueConstraint,
    false,
    func,
    true,
)

from sqlalchemy.dialects.postgresql import UUID

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
    Column("training_replaces_slot", Boolean, nullable=False, server_default=true()),
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
    Column("training_replaces_slot", Boolean, nullable=False, server_default=true()),
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
    # The qualification (skjenker / skiftleder / kokk) this person is in
    # training for on this shift; null for a normal place.
    Column("training_for", Text),
    # "Kan ikke i dag": the person stays on the shift, marked, and the place
    # is open to others; they come off when someone takes it.
    Column("cannot_attend", Boolean, nullable=False, server_default=false()),
    # Optional note with a swap request or "kan ikke". Only for the leaders,
    # and to be deleted after the shift.
    Column("status_message", Text),
    Column("status_changed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("slot_id", "volunteer_id", name="shift_assignments_slot_volunteer_key"),
)

# Periods when a group has no shifts (holidays). The semester generator skips
# every date from starts_on to ends_on, both included.
shift_closed_periods = Table(
    "shift_closed_periods",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column("group_id", BigInteger, ForeignKey("public.groups.id"), nullable=False),
    Column("starts_on", Date, nullable=False),
    Column("ends_on", Date, nullable=False),
    Column("label", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("ends_on >= starts_on", name="shift_closed_periods_dates_ordered"),
)

# Per-group switches for the app. A group's shifts are only in the app when
# app_enabled is set, so the app can ship with every screen and groups are
# turned on from Personal.
shift_group_settings = Table(
    "shift_group_settings",
    public_metadata,
    Column("group_id", BigInteger, ForeignKey("public.groups.id"), primary_key=True),
    Column("app_enabled", Boolean, nullable=False),
    # Also show this group's places that are open for samfunnettjeneste to
    # every volunteer (not only to the group's members).
    Column("samf_enabled", Boolean, nullable=False),
    # Shown on the shift in the app ("Møt skiftleder 30 minutter før").
    Column("meeting_point", Text),
    # Check brannkurs and barkurs before the app lets someone take this group's
    # places. Off for new groups until the group has set its courses up.
    Column("courses_enabled", Boolean, nullable=False, server_default=false()),
    # Training shifts a person does before a leader can approve them.
    Column("training_shifts_needed", Integer, nullable=False, server_default="3"),
    # Skiftledere approved in a participating sibling bar may lead shifts here.
    Column("sibling_skiftleder_allowed", Boolean, nullable=False, server_default=false()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Allocation status per group and week. Without a row the current week and
# earlier are "published" and later weeks "open" (effective_week_status).
# "open" = taking wishes, "allocating" = leaders are allocating; volunteers
# do not see the draft. A leader publishes a week with an explicit row.
shift_week_status = Table(
    "shift_week_status",
    public_metadata,
    Column("group_id", BigInteger, ForeignKey("public.groups.id"), primary_key=True),
    # The Monday of the week, Oslo time.
    Column("week_start", Date, primary_key=True),
    Column("status", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Weekday answers per semester: vil (gjerne) / kan (hvis nødvendig) / nei.
# No row = not answered. "nei" never carries a reason.
shift_availability_rules = Table(
    "shift_availability_rules",
    public_metadata,
    Column(
        "volunteer_id",
        BigInteger,
        ForeignKey("public.volunteer_records.id"),
        primary_key=True,
    ),
    Column("semester", Integer, primary_key=True),
    Column("weekday", SmallInteger, primary_key=True),
    Column("answer", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Per-shift answer that overrides the weekday rule.
shift_wishes = Table(
    "shift_wishes",
    public_metadata,
    Column(
        "shift_id",
        BigInteger,
        ForeignKey("public.shifts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "volunteer_id",
        BigInteger,
        ForeignKey("public.volunteer_records.id"),
        primary_key=True,
    ),
    Column("answer", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# What a person may do on shifts. Rows are per bar: a skiftleder or kokk row
# is valid in its own group (see the HANDOFF decisions for the sibling rules).
# "training" rows count training shifts; "approved" rows are the qualification.
volunteer_qualifications = Table(
    "volunteer_qualifications",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "volunteer_id",
        BigInteger,
        ForeignKey("public.volunteer_records.id"),
        nullable=False,
    ),
    # skjenker / skiftleder / kokk.
    Column("qualification", Text, nullable=False),
    # training / approved.
    Column("status", Text, nullable=False),
    Column("group_id", BigInteger, ForeignKey("public.groups.id"), nullable=False),
    # Copied from the bar's setting when training starts; null for rows
    # granted directly and for migrated rows.
    Column("training_target", Integer),
    # "Tar opplæring": whether this skiftleder or kokk takes trainees.
    Column("takes_trainees", Boolean, nullable=False, server_default=false()),
    # Auth user who approved; null for migrated rows.
    Column("approved_by", UUID(as_uuid=True)),
    Column("approved_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "volunteer_id",
        "qualification",
        "group_id",
        name="volunteer_qualifications_volunteer_qualification_group_key",
    ),
    CheckConstraint(
        "qualification IN ('skjenker', 'skiftleder', 'kokk')",
        name="volunteer_qualifications_qualification_valid",
    ),
    CheckConstraint(
        "status IN ('training', 'approved')",
        name="volunteer_qualifications_status_valid",
    ),
)
