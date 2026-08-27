"""The capability matrix as data — SPEC-003 §4.1 (A1, A2, A3) + the entity classification (C10).

**Why this file exists at all.** `ONBOARDING:244-265` has 20 data rows in which every action is an
English verb phrase ("Manage vendors", "Invite users (admin/staff)"), values are three-valued
(`✓`/`✗`/`scoped`), and two cells carry prose caveats *inside the cell*. §9.4 step 3 then says to
"look up `(role, action)` in the capability matrix" — but there is nothing to look up (F2).
Encoding it is spec work, not transcription, and this module is the canonical vocabulary the rest
of the phase resolves against.

**Row 8 is deliberately split in two.** D12 gives staff read access to some vendor fields and no
write access at all, which one three-valued cell cannot express. That makes 21 keys for 20 rows;
the `row` field preserves traceability back to the source, and `test_all_twenty_rows_covered`
asserts on the set of row numbers rather than the key count for exactly that reason.

**The two hoisted rules are functions, not data**, because neither is expressible as a grant:
R1 depends on *who the target is* relative to the actor, and R2 depends on the actor and target
being the same person. A lookup table has no place to put either.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "ENTITY_CLASSES",
    "EXTRA_RULES",
    "MATRIX",
    "Access",
    "ActionSpec",
    "EntityClass",
    "Grant",
]


class Grant(StrEnum):
    ALLOW = "allow"     # ✓
    DENY = "deny"       # ✗
    SCOPED = "scoped"   # allowed only within membership_property_scopes


class Access(StrEnum):
    """Which route class an action may be declared on.

    The distinction is settled here rather than at Step 5 because §9.4 leaves undefined what
    happens when a grant is `SCOPED` but `target_property` is `None`, and the obvious reading —
    "None → deny" — is **wrong**: `GET /tasks` has no single target, so every collection route
    would 403 for staff and the query-layer filtering would be unreachable code (N5).
    """

    ITEM = "item"              # operates on one record; target_property REQUIRED when SCOPED
    COLLECTION = "collection"  # list/index; authorized by scoped_property_ids() at the query
    ACCOUNT = "account"        # account-level; no property target exists
    # **Added by SPEC-003 G13.5 — a class §4.1 does not have, and needs.**
    #
    # `ITEM`, `COLLECTION` and `ACCOUNT` all presuppose an account: they differ only in whether a
    # *property* target exists. Three screens in this phase run **before or across** account
    # selection and fit none of them:
    #
    #   onboarding steps 1-2  the account does not exist yet
    #   invite acceptance     the invitee is not a member of anything yet
    #   the account switcher  the target is an account other than the current one
    #
    # Forcing them into `ACCOUNT` would 403 every one, because the enforcement dependency resolves
    # an account before consulting the matrix. `SESSION` means "authorised by being a signed-in
    # **user**, not by a role within an account" — so the matrix is not consulted at all, and the
    # route is responsible for whatever authorisation it does need (a valid invite token, a
    # membership in the account being switched to).
    SESSION = "session"


@dataclass(frozen=True)
class ActionSpec:
    key: str
    row: int                   # ONBOARDING §9.2 row number — traceability to the source
    owner: Grant
    admin: Grant
    staff: Grant
    access: Access
    rule: str | None = None    # hoisted prose caveat; see EXTRA_RULES


def _spec(key, row, owner, admin, staff, access, rule=None) -> ActionSpec:
    return ActionSpec(key, row, owner, admin, staff, access, rule)


_A, _D, _S = Grant.ALLOW, Grant.DENY, Grant.SCOPED
_ITEM, _COLL, _ACCT = Access.ITEM, Access.COLLECTION, Access.ACCOUNT

MATRIX: dict[str, ActionSpec] = {
    "property.view":       _spec("property.view",       1,  _A, _A, _S, _ITEM),
    "property.edit":       _spec("property.edit",       2,  _A, _A, _D, _ITEM),
    # plan-gated in Phase 3 (D18) — the entitlements gate is separate from this one (D10).
    "property.add":        _spec("property.add",        3,  _A, _A, _D, _ACCT),
    "property.delete":     _spec("property.delete",     4,  _A, _A, _D, _ITEM),
    "task.manage":         _spec("task.manage",         5,  _A, _A, _S, _ITEM),
    "issue.manage":        _spec("issue.manage",        6,  _A, _A, _S, _ITEM),
    "inventory.manage":    _spec("inventory.manage",    7,  _A, _A, _S, _ITEM),
    # **SPEC-004 — row 7 split, the fourth split and the same precedent as row 8 (D12).** Documents
    # live under `inventory.manage`, which is `SCOPED` for staff and correctly so: a housekeeper
    # may see an appliance manual for a property they cover. What they may not do is decide *who
    # else* sees a document — that is the owner's call, and a three-valued cell cannot say "you may
    # read these rows but not administer who reads them".
    #
    # `_ACCT` rather than `_ITEM`: granting is not an operation on one property, and
    # `test_scoped_grants_are_never_declared_on_account_routes` keeps `SCOPED` off account routes,
    # which is why staff is `_D` here rather than a narrowed grant. Admins are included on the
    # owner's instruction — the same pairing as `member.manage`.
    "document.grant":      _spec("document.grant",      7,  _A, _A, _D, _ACCT,
                                 rule="SPEC-004: owner/admin decide who sees each document; "
                                      "staff never administer access"),
    "vendor.view_contact": _spec("vendor.view_contact", 8,  _A, _A, _S, _ITEM,
                                 rule="D12: staff read-only, contact fields only"),
    "vendor.manage":       _spec("vendor.manage",       8,  _A, _A, _D, _ITEM,
                                 rule="D12: split from row 8 — staff never write vendors"),
    "finance.view":        _spec("finance.view",        9,  _A, _A, _D, _ACCT),
    "member.manage":       _spec("member.manage",       10, _A, _A, _D, _ACCT),
    # **U6b — row 10 split, on the row-8 precedent.** §4.1 classifies `staff` as `PERSONNEL` with
    # the rule *"Staff may see their own record; never others'"*, and no key expressed it: G6
    # declared every HR route `member.manage`, so staff saw **nothing**, their own record
    # included. Fail-closed and stricter than the spec, recorded as U6.
    #
    # `_A` for staff, not `_S`, and the distinction is the whole design. `SCOPED` means "filtered
    # to the properties in your scope", which is not the rule here — the rule is "filtered to the
    # row that is you", and a staff member's own HR record has nothing to do with which homes they
    # cover. `test_scoped_grants_are_never_declared_on_account_routes` also forbids `SCOPED` on an
    # `ACCOUNT` route, correctly: there is no property target to scope by. The row-narrowing is
    # done by `query_scope._personnel_criteria` filtering on `Staff.user_id`, which is exactly
    # where §9.4 step 4 says it belongs — *"at the query layer, not post-hoc"*. Same shape as row
    # 20's `gateway.link_self`: allowed to everyone, narrowed to self by the mechanism.
    "staff.view_own":      _spec("staff.view_own",      10, _A, _A, _A, _ACCT,
                                 rule="F2d: own record only — narrowed by PERSONNEL at the "
                                      "query layer, never by this grant"),
    # **U6b — row 5 split.** Templates generate tasks, so G6 declared `/templates/` with
    # `task.manage`, which is `SCOPED` for staff — and that granted staff the ability to *create
    # and delete* the templates that drive everyone's work, which row 5 never intended. Running a
    # template is task work; managing one is automation configuration. `/{slug}/run` keeps
    # `task.manage`; the CRUD routes move here.
    #
    # This also retires `query_scope._ACCOUNT_LEVEL_EXEMPT`'s `Template`/`TemplateItem` entries:
    # U7 had to exempt them because `task.manage` let staff reach `/templates/`, so denying the
    # rows would have broken a route the matrix still permitted. Redeclaring the route is what
    # makes denying the rows correct — U6 resolves U6.
    "automation.manage":   _spec("automation.manage",   5,  _A, _A, _D, _ACCT,
                                 rule="U6b: split from row 5 — staff may run a template, "
                                      "never manage one"),
    "invite.create":       _spec("invite.create",       11, _A, _A, _D, _ACCT),
    "invite.modify":       _spec("invite.modify",       12, _A, _A, _D, _ACCT),
    "member.change_role":  _spec("member.change_role",  13, _A, _A, _D, _ACCT, rule="R1"),
    "account.transfer":    _spec("account.transfer",    14, _A, _D, _D, _ACCT),
    "billing.manage":      _spec("billing.manage",      15, _A, _D, _D, _ACCT),
    "account.delete":      _spec("account.delete",      16, _A, _D, _D, _ACCT),
    "audit.view":          _spec("audit.view",          17, _A, _A, _D, _ACCT),
    "ai.use":              _spec("ai.use",              18, _A, _A, _S, _COLL),
    "export.data":         _spec("export.data",         19, _A, _A, _D, _ACCT),
    "gateway.link_self":   _spec("gateway.link_self",   20, _A, _A, _A, _ACCT, rule="R2"),
}


# --------------------------------------------------------------------------------------
# The two hoisted caveats (F2) — prose that no lookup table can hold.
# --------------------------------------------------------------------------------------


def _rule_change_role(
    *,
    actor_role: str,
    actor_membership_id,
    target_role: str,
    target_membership_id,
) -> bool:
    """R1 — row 13's "(not owner's, not own)". True when the change is permitted.

    Two prohibitions with different subjects, which is why one boolean cell cannot hold them:

    - **An admin may not change the active owner's role.** Without this an admin demotes the
      owner and takes the account — the escalation this whole row exists to prevent.
    - **Nobody may change their own role.** Ownership moves only by D2 transfer, so a
      self-promotion path would be a second, unaudited route to ownership.

    The owner may change anyone else's role freely; staff never reach here at all, because the
    matrix already denies them row 13.
    """
    if actor_membership_id == target_membership_id:
        return False
    if actor_role == "admin" and target_role == "owner":
        return False
    return actor_role in ("owner", "admin")


def _rule_link_self(*, actor_role: str, actor_membership_id, target_membership_id) -> bool:
    """R2 — row 20's "(scoped access applies)". True when the link is permitted.

    Linking is **self-only for every role**, owner included: no role may link a chat gateway on
    another user's behalf, because doing so would bind someone else's identity to a sender the
    linker controls.

    The cell's other half — that linking grants no *additional* data access — is not enforceable
    here. It holds because every resolved bot request re-enters `require_permission` with the
    membership's own role (Step 16); this function only decides who may create the link.
    `actor_role` is accepted and deliberately unused so the signature cannot silently diverge
    from R1's, and so a future per-role exception has an obvious place to land.
    """
    return actor_membership_id == target_membership_id


EXTRA_RULES = {"R1": _rule_change_role, "R2": _rule_link_self}


# --------------------------------------------------------------------------------------
# Entity classification — closing F2d's silence and N4's requirement (C10).
# --------------------------------------------------------------------------------------


class EntityClass(StrEnum):
    """§4.1's six classes, **plus a seventh the spec was missing.** Every mapped model lands in
    exactly one.

    N4: *"Do not scope only the property-bearing entities. Vendors, contracts, budgets, notes and
    personnel records have no `property_id`; threading a property set past them silently allows
    them."*
    """

    PROPERTY_SCOPED = "property_scoped"   # visible if property_id ∈ scope, money redacted (D14)
    PROPERTY_LINKED = "property_linked"   # vendor — contact fields only, read-only (D12)
    FLAGGED = "flagged"                   # document — per-person grant AND property-scoped
    ACCOUNT_LEVEL = "account_level"       # ✗ for staff
    PERSONNEL = "personnel"               # own record only, never others' (F2d)
    GLOBAL = "global"                     # not tenant data; unaffected

    # **The seventh class, added to close SPEC-003's U6 residual.**
    #
    # `NO_CLASS_FITS["Template"]` named this gap at G17 and U6b confirmed rather than closed it:
    # *"what is missing is a class for 'account-wide, not sensitive, staff use it'."* Templates are
    # account-wide (no `property_id`), staff legitimately **use** them — matrix row 5 grants running
    # one, and `run_template` resolves by slug so running requires reading the row — and their
    # fields are a name, a description and checklist items, the same class of content as the Tasks
    # they generate, which staff already see.
    #
    # Every existing class was wrong for that. `ACCOUNT_LEVEL` means "✗ for staff" and enforcing it
    # would break a capability the matrix deliberately grants; §4.1's own account-level list
    # (budget / contract / recurring_expense / transaction / configuration / note / book) never
    # contained `template`, so the classification had no source authority behind it either.
    # `PROPERTY_SCOPED` cannot apply to a row with no property. So the model sat classified-but-
    # exempted, and `_ACCOUNT_LEVEL_EXEMPT` carried two entries whose only justification was that
    # the label was a lie.
    #
    # **Read, not decorative** — that is the lesson U7 exists to enforce. `query_scope` derives
    # this class's members and applies *no row filter* to them, deliberately and visibly, so the
    # absence of filtering is a decision the code states rather than an omission nobody notices.
    # Access control for these models lives in the route declarations, which is the right layer:
    # the rule is a **verb** distinction (staff may run a template, never manage one — row 5's
    # `task.manage` vs `automation.manage`), and a query layer can only express row visibility.
    ACCOUNT_SHARED = "account_shared"     # account-wide, not sensitive, staff use it (U6)


def _entity_classes() -> dict[type, EntityClass]:
    """Built in a function so the model imports stay local to this table.

    §4.1 names about 22 models; the tree has 42 mapped classes. The 20 additions below are the
    C10 correction — and they are not filler. `InsurancePolicy` is money-bearing and
    property-scoped; `VendorRating` is a model D12 denies staff by name; `PriceEntry` and
    `ConsumablePriceEntry` each carry a `Money` column one relationship hop from a row staff may
    see, which is F4's shape reached through a child table.
    """
    from mihomes.models.account import Account
    from mihomes.models.ai_conversation import AIConversation
    from mihomes.models.ai_usage import AIUsageEvent, AIUsageRollup
    from mihomes.models.alert import Alert
    from mihomes.models.appointment import Appointment
    from mihomes.models.asset import Asset, PriceEntry
    from mihomes.models.audit_log import AuditLog
    from mihomes.models.book import Book
    from mihomes.models.budget import Budget, Transaction
    from mihomes.models.configuration import Configuration
    from mihomes.models.consumable import Consumable, ConsumablePriceEntry
    from mihomes.models.contract import Contract
    from mihomes.models.document import Document
    from mihomes.models.document_access import DocumentAccess
    from mihomes.models.email_suppression import EmailSuppression
    from mihomes.models.event import Event, EventGuest, Guest
    from mihomes.models.insurance import InsurancePolicy
    from mihomes.models.invite import Invite
    from mihomes.models.issue import Issue
    from mihomes.models.membership import Membership, MembershipPropertyScope
    from mihomes.models.note import Note
    from mihomes.models.onboarding_state import OnboardingState
    from mihomes.models.processed_webhook_event import ProcessedWebhookEvent
    from mihomes.models.property import Property
    from mihomes.models.recurring_expense import RecurringExpense
    from mihomes.models.session import Session
    from mihomes.models.space import Space
    from mihomes.models.staff import Staff
    from mihomes.models.staff_pto import StaffPTORequest
    from mihomes.models.tag import Tag, TagAssignment
    from mihomes.models.task import Task, TaskSchedule
    from mihomes.models.telegram_link import TelegramLink
    from mihomes.models.template import Template, TemplateItem
    from mihomes.models.user import User
    from mihomes.models.vendor import Vendor
    from mihomes.models.vendor_rating import VendorRating
    from mihomes.models.waitlist import Waitlist
    from mihomes.models.work_order import WorkOrder
    from mihomes.models.zone import Zone

    scoped = EntityClass.PROPERTY_SCOPED
    account = EntityClass.ACCOUNT_LEVEL

    return {
        # Property-scoped — §4.1's own list, plus the child and event-adjacent tables it omits.
        Task: scoped,
        TaskSchedule: scoped,
        Issue: scoped,
        WorkOrder: scoped,
        Asset: scoped,
        PriceEntry: scoped,
        Consumable: scoped,
        ConsumablePriceEntry: scoped,
        Zone: scoped,
        Space: scoped,
        Appointment: scoped,
        Event: scoped,
        EventGuest: scoped,
        Guest: scoped,
        Alert: scoped,
        # The property itself is scoped by its own id — row 1 is `scoped` for staff.
        Property: scoped,

        Vendor: EntityClass.PROPERTY_LINKED,
        Document: EntityClass.FLAGGED,
        # **`ACCOUNT_LEVEL`, and the exemption that comes with it is the point.** A grant row is
        # the *answer* to "may this person see this document", so `_document_criteria` reads it to
        # decide — which makes it the same shape as `Membership`/`MembershipPropertyScope`: a table
        # the authorization primitive consults, and therefore one that cannot be subject to its own
        # filter without the question becoming circular. Registered in
        # `query_scope._ACCOUNT_LEVEL_EXEMPT` for exactly that reason, alongside them.
        #
        # `ACCOUNT_LEVEL` rather than a new class because the row genuinely is account-level
        # configuration: staff never read the grant table as *content*, only ever as the thing
        # gating something else. Nothing surfaces a grant to a staff member, and the exemption
        # means nothing needs to.
        DocumentAccess: account,

        # Account-level. Budget/Contract/RecurringExpense/Book DO carry property_id — they are
        # denied to staff by row 9 policy, not for want of a property to scope by (C10).
        Budget: account,
        Transaction: account,
        Contract: account,
        RecurringExpense: account,
        Configuration: account,
        Note: account,
        # **Reclassified by G17 from ACCOUNT_LEVEL.** §4.1 listed `book` as account-level, and
        # C10 already recorded that the rationale was wrong: `Book` carries `property_id`, the
        # library *is* inventory, and `books.py`/`library.py` declare `inventory.manage` — which
        # is `SCOPED` for staff. That contradiction was not academic: G17's probe found
        # `/library/` returning another property's books to a scoped staff member, because
        # `ACCOUNT_LEVEL` has no query-layer enforcement and the route's own grant let them in.
        # Classifying it the way the data and the route already agree closes the leak through the
        # existing filter, with no route change — and `test_library_scoped_for_staff` pins it.
        Book: scoped,
        # Financial records §4.1 never classified.
        InsurancePolicy: account,
        # D12 denies staff ratings by name, yet the model was unclassified.
        VendorRating: account,
        Tag: account,
        TagAssignment: account,
        # **Reclassified from ACCOUNT_LEVEL — SPEC-003 U6's last code item.** C10 put them here and
        # `NO_CLASS_FITS` recorded at G17 that the label was wrong: §4.1's account-level list does
        # not contain `template`, staff legitimately run templates (row 5), and a template's fields
        # are the same class of content as the Tasks it generates. `ACCOUNT_SHARED` is the class
        # that describes them. The two `_ACCOUNT_LEVEL_EXEMPT` entries that existed only to
        # neutralise the wrong label retire with this change.
        Template: EntityClass.ACCOUNT_SHARED,
        TemplateItem: EntityClass.ACCOUNT_SHARED,
        AIConversation: account,
        AuditLog: account,
        Account: account,
        Membership: account,
        MembershipPropertyScope: account,
        Invite: account,
        # Onboarding progress is account configuration, not property data — staff never see it,
        # and the only reader is the wizard resolving the signed-in owner's own account.
        OnboardingState: account,
        # A chat link is identity plumbing, not estate data. Row 20 (`gateway.link_self`) lets a
        # staff member create *their own*, but nobody reads the table as content — so
        # account-level, and never surfaced to a staff query.
        TelegramLink: account,

        Staff: EntityClass.PERSONNEL,
        StaffPTORequest: EntityClass.PERSONNEL,

        User: EntityClass.GLOBAL,
        Session: EntityClass.GLOBAL,
        Waitlist: EntityClass.GLOBAL,

        # SPEC-004 B7 — the webhook idempotency ledger, and `GLOBAL` is the honest label rather
        # than a convenient one. A raw Stripe webhook is read and written *before* an account is
        # known: `NormalizedEvent` carries a provider customer id and nothing else, and resolving
        # it is `BillingService`'s job (D2). That is the same condition the registry already
        # gives for `sessions` — *"read or written BEFORE account context exists"* — and it is
        # what `GLOBAL`'s `UNFILTERED_CLASSES` entry already says in words.
        #
        # The row also carries a nullable `account_id`, which is *not* tenancy: it records which
        # account an event resolved to, may legitimately stay NULL when it resolved to none, and
        # is never used to decide who may read the row. Nobody reads this table as content — it
        # exists so an event is processed once.
        ProcessedWebhookEvent: EntityClass.GLOBAL,

        # SPEC-005 §4.1 — the suppression list. `GLOBAL`, and for a reason unlike either
        # existing carve-out: `sessions` and `processed_webhook_events` are global because
        # they are read *before* account context exists. This one is global because
        # **suppression is a property of an address, not of an account**. Someone who
        # unsubscribes or files a complaint must stay suppressed when they later appear
        # under a second account — as an invited staff member, a second signup, a vendor
        # contact. Per-tenant scoping would re-mail a complainer the first time they were
        # invited elsewhere, which is how a sending domain gets blocklisted.
        #
        # The row carries no `account_id` at all, so there is nothing here to filter on:
        # unlike the webhook ledger, this needs no "the nullable column is not tenancy"
        # caveat. An address and a reason, and nothing else about the person.
        EmailSuppression: EntityClass.GLOBAL,


        # SPEC-004 §4.2 — the AI usage meter. `ACCOUNT_LEVEL` ("✗ for staff"), and the fit is
        # exact rather than convenient: these rows are **billing data**. `calls_used` against a
        # plan's cap tells a staff member which tier the household pays for, and the event log's
        # `entry_point` traces who used the assistant and when — both account-level facts about
        # the *owner's* spending, not estate data a housekeeper needs to do their job.
        #
        # Row 15 (`billing.manage`) is owner-only, and D10 keeps RBAC and entitlements as
        # separate gates: an Estate plan buys more calls, never the right to read whose they
        # were.
        AIUsageEvent: account,
        AIUsageRollup: account,
    }


ENTITY_CLASSES: dict[type, EntityClass] = _entity_classes()
