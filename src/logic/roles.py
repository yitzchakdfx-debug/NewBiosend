"""Single source of truth for user roles and what each role may do.

Before this module the role list was duplicated in four places (models, the
SQLite CHECK constraint, DatabaseManager and the user-management combo box) and
enforcement was a scatter of ``role == "Admin"`` comparisons across the UI.
Adding the Maintenance role required by spec Rev.1.1 §1.1.4 made that
untenable: a role is only safe to add once there is one list to extend and one
predicate to ask.

``Role`` derives from ``str`` so existing string comparisons and the values
already stored in the ``users`` table keep working unchanged.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """A user privilege level. The value is what is stored in the database."""

    OPERATOR = "Operator"
    TECHNICIAN = "Technician"
    MAINTENANCE = "Maintenance"
    ADMIN = "Admin"


#: Every valid role value, in ascending order of privilege. This is the list
#: the DB CHECK constraint and the user-management combo box are built from.
ALL_ROLES: tuple[str, ...] = tuple(r.value for r in Role)


class Capability(str, Enum):
    """A single thing a user may be permitted to do.

    Gate behaviour on these, never on a role name — that is what lets a new
    role be added by editing one table instead of auditing every call site.
    """

    RUN_SEQUENCE = "run_sequence"                 # start a normal production run
    RUN_SINGLE_STEP = "run_single_step"           # execute one step out of sequence
    SELECT_STEPS = "select_steps"                 # tick/untick which steps run
    GENERATE_REPORTS = "generate_reports"         # produce/export PDF and XML
    ARCHIVE_RUN = "archive_run"                   # persist the run to the database
    EDIT_LIMITS = "edit_limits"                   # change limits/params of a test
    EDIT_TEMPLATES = "edit_templates"             # change report templates
    MANAGE_VERSIONS = "manage_versions"           # import/edit/delete test versions
    MANAGE_USERS = "manage_users"                 # create/delete/modify users
    VIEW_AUDIT_LOG = "view_audit_log"             # open the audit log
    EDIT_CONNECTIONS = "edit_connections"         # change instrument connections
    VIEW_MEASURED_DETAIL = "view_measured_detail" # see Min/Max/Value columns
    VIEW_TRACE_LOG = "view_trace_log"             # see the live trace pane


_OPERATOR: frozenset[Capability] = frozenset(
    {
        Capability.RUN_SEQUENCE,
        Capability.GENERATE_REPORTS,
        Capability.ARCHIVE_RUN,
    }
)

_TECHNICIAN: frozenset[Capability] = _OPERATOR | {Capability.SELECT_STEPS}

# Maintenance is deliberately NOT a superset of Technician. Spec Rev.1.1
# §1.1.4.3: it may run individual scenarios outside the production flow, and
# "Test reports shall not be generated in this mode" — so it gains diagnostic
# visibility but loses report generation and run archiving. Without that, bench
# troubleshooting would pollute the production record with real-looking reports.
_MAINTENANCE: frozenset[Capability] = frozenset(
    {
        Capability.RUN_SEQUENCE,
        Capability.RUN_SINGLE_STEP,
        Capability.SELECT_STEPS,
        Capability.VIEW_MEASURED_DETAIL,
        Capability.VIEW_TRACE_LOG,
    }
)

_ADMIN: frozenset[Capability] = frozenset(Capability)

_MATRIX: dict[Role, frozenset[Capability]] = {
    Role.OPERATOR: _OPERATOR,
    Role.TECHNICIAN: _TECHNICIAN,
    Role.MAINTENANCE: _MAINTENANCE,
    Role.ADMIN: _ADMIN,
}


def coerce_role(raw: str) -> Role | None:
    """Return the `Role` for `raw` (case-insensitive), or None if unknown."""
    try:
        return Role(str(raw).strip().title())
    except ValueError:
        return None


def capabilities(role: str) -> frozenset[Capability]:
    """Capabilities granted to `role`. An unknown role gets none."""
    resolved = coerce_role(role)
    return _MATRIX[resolved] if resolved is not None else frozenset()


def can(role: str, capability: Capability) -> bool:
    """True if `role` is permitted to perform `capability`.

    Fails closed: an unrecognised or empty role is denied everything.
    """
    return capability in capabilities(role)
