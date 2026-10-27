"""Tables owned by the courses module."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    func,
)

from app.db.metadata import public_metadata

courses = Table(
    "courses",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text),
    Column("created_at", DateTime(timezone=True)),
)

course_completions = Table(
    "course_completions",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column("volunteer_id", BigInteger, ForeignKey("public.volunteer_records.id"), nullable=False),
    Column("course_id", BigInteger, ForeignKey("public.courses.id"), nullable=False),
    Column("completed_semester", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True)),
)

group_course_requirements = Table(
    "group_course_requirements",
    public_metadata,
    Column("course_id", BigInteger, ForeignKey("public.courses.id"), primary_key=True),
    Column("group_id", BigInteger, ForeignKey("public.groups.id"), primary_key=True),
)

# Dated course sessions people can sign up for (brannkurs, barkurs, ...).
# A completed course is still recorded in course_completions per semester;
# a sign-up only says the person is booked on a session.
course_sessions = Table(
    "course_sessions",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column("course_id", BigInteger, ForeignKey("public.courses.id"), nullable=False),
    Column("starts_at", DateTime(timezone=True), nullable=False),
    Column("location", Text),
    Column("capacity", Integer, nullable=False),
    Column("is_cancelled", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

course_session_signups = Table(
    "course_session_signups",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "session_id",
        BigInteger,
        ForeignKey("public.course_sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("volunteer_id", BigInteger, ForeignKey("public.volunteer_records.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("session_id", "volunteer_id", name="course_session_signups_session_volunteer_key"),
)
