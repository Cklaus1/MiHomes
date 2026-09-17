#!/usr/bin/env python
"""Create Free- and Pro-tier tester accounts in `mihomes_dev`, with mock data.

    py scripts/seed_tiers.py            # create the two testers
    py scripts/seed_tiers.py --reset    # put them back on their plan after a trial starts

**Why.** `mlim@fusen.world` owns an Estate account, so the Estate experience is already
visible. Seeing what a *paying* or *non-paying* customer sees needs accounts on the other two
plans, and nothing in the product creates one: `onboarding_service.create_account_step`
hardcodes `plan="free"`, and `plan` is otherwise written only by the Stripe webhook — which
needs a Stripe account that does not exist yet (SPEC-004 O1).

So this writes the rows the webhook would, the same way `dev_setup.py` writes the rows the
OAuth callback would.

**`subscription_status` is set to `active`, and that is not decoration.** `limits_for` maps
status → effective plan, and `canceled`, `unpaid` and `incomplete` all collapse to `free`. A
`pro` account left at status `NULL` happens to resolve as Pro today, but it is a Pro account
that has never been billed — one webhook away from silently reading as Free. Setting it
`active` states the intent the rest of the code reads.

**The Free tester stops being Free the moment you push it past a limit.** That is not a bug
here — `_check_home_entitlement` starts the no-card trial on the first denied `property.add`
(§4.2), so trying to add a second home converts the account to Pro-on-trial. `--reset` puts it
back without discarding the seeded data.

**Local development only.** Like `dev_setup.py` it mints sessions without authenticating
anyone, and it refuses to run against a database not named `mihomes_dev`.
"""

from __future__ import annotations

import os
import sys

DB = "mihomes_dev"
URL = f"postgresql+psycopg://postgres@localhost:5432/{DB}"

if os.environ.get("DATABASE_URL") and not os.environ["DATABASE_URL"].endswith(DB):
    sys.exit(f"refusing to run: DATABASE_URL points somewhere other than {DB}")
os.environ["DATABASE_URL"] = URL

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

#: The password for both testers. Local-only, and printed at the end — these accounts exist to
#: be logged into by hand. 12 characters is `MIN_PASSWORD_LENGTH`.
PASSWORD = "TesterPass123!"


# Each tester gets its **own** user. `uq_membership_one_owner` permits one active owner per
# account, so reusing an existing user as owner of a second account violates it — `dev_setup.py`
# records finding that by running twice.
TESTERS = [
    {
        "email": "free@test.local",
        "name": "Free Tester",
        "slug": "free-tester",
        "account": "Free Tier Tester",
        "plan": "free",
    },
    {
        "email": "pro@test.local",
        "name": "Pro Tester",
        "slug": "pro-tester",
        "account": "Pro Tier Tester",
        "plan": "pro",
    },
]


def seed_content(session, plan: str) -> str:
    """Mock data sized to the plan. Returns a one-line summary.

    **Free gets exactly one property because one is its cap.** Seeding two would either be
    refused by `_check_home_entitlement` or — worse — silently start the account's free trial
    (`maybe_start_trial` fires on the first denied `property.add`), leaving a "Free" tester
    that is actually mid-trial on Pro entitlements. The point of these accounts is to show the
    plans as they are, so the Free account stays at its ceiling rather than through it.
    """
    from mihomes.models.issue import IssueSeverity
    from mihomes.models.property import PropertyType
    from mihomes.models.task import TaskPriority
    from mihomes.services.issue import create_issue
    from mihomes.services.property import create_property
    from mihomes.services.staff import create_staff
    from mihomes.services.task import create_task
    from mihomes.services.vendor import create_vendor

    if plan == "free":
        homes = [("Oak Street House", "412 Oak Street, Portland, OR", PropertyType.PRIMARY)]
    else:
        homes = [
            ("Harbour View", "8 Harbour Road, Newport, RI", PropertyType.PRIMARY),
            ("Lakeside Cabin", "77 Birch Trail, Lake Placid, NY", PropertyType.VACATION),
            ("Dune Cottage", "3 Dune Lane, Truro, MA", PropertyType.SEASONAL),
        ]

    created = []
    for name, address, kind in homes:
        created.append(create_property(session, name, address=address, property_type=kind))
    session.flush()

    first = created[0]

    for title, priority in (
        ("Replace furnace filter", TaskPriority.MEDIUM),
        ("Test smoke alarms", TaskPriority.HIGH),
        ("Clear the gutters", TaskPriority.LOW),
    ):
        create_task(session, title, first.slug, priority=priority)

    for title, severity in (
        ("Dripping tap in the main bathroom", IssueSeverity.MEDIUM),
        ("Porch light flickers", IssueSeverity.LOW),
    ):
        create_issue(session, title, first.slug, severity=severity)

    create_staff(session, "Dana Whitfield", role="housekeeper", property_id_or_slug=first.slug)

    # Pro-only extras, so the two accounts differ in content as well as in what the gates
    # allow: more homes, more people, and vendors — `vendor.rate` and `work_order.schedule`
    # are Pro entitlements, and a rating needs a vendor to hang off.
    if plan != "free":
        create_staff(session, "Ellis Barnard", role="groundskeeper",
                     property_id_or_slug=created[1].slug)
        create_task(session, "Winterise the cabin", created[1].slug, priority=TaskPriority.HIGH)
        create_task(session, "Rake the beach path", created[2].slug, priority=TaskPriority.LOW)
        create_issue(session, "Deck board is loose", created[2].slug,
                     severity=IssueSeverity.MEDIUM)

        create_vendor(session, "Kestrel Plumbing", contact_name="Ray Kestrel",
                      phone="555-0410", service_categories=["plumbing"])
        create_vendor(session, "Birchwood Landscaping", contact_name="Nina Ho",
                      phone="555-0411", service_categories=["landscaping"])

    homes_n = len(created)
    staff_n = 1 if plan == "free" else 2
    tasks_n = 3 if plan == "free" else 5
    issues_n = 2 if plan == "free" else 3
    vendors_n = 0 if plan == "free" else 2
    return (f"{homes_n} propert{'y' if homes_n == 1 else 'ies'}, {tasks_n} tasks, "
            f"{issues_n} issues, {staff_n} staff, {vendors_n} vendors")


def reset_plans(engine) -> None:
    """Put both testers back on their intended plan — `py scripts/seed_tiers.py --reset`.

    **The Free tester does not stay Free if you use it.** Exceeding a plan limit is what starts
    the app's no-card trial: `_check_home_entitlement` denies `property.add`, then calls
    `maybe_start_trial`, which is §4.2's deliberate design — *"a trial that starts at signup is
    often burned before the 2nd home appears"*. Measured here: adding a second home to the Free
    tester left it `plan='pro'` with `trial_ends_at` set, i.e. no longer a Free account at all.

    That is the product behaving correctly, and it is also a tester that has stopped testing what
    it was made for. This puts it back rather than requiring the accounts be dropped and reseeded
    (which would discard the mock data along with them).
    """
    with engine.begin() as c:
        for spec in TESTERS:
            c.execute(
                text(
                    "UPDATE accounts SET plan = :p, subscription_status = 'active',"
                    " trial_ends_at = NULL, trial_used_at = NULL WHERE slug = :s"
                ),
                {"p": spec["plan"], "s": spec["slug"]},
            )
            row = c.execute(
                text(
                    "SELECT a.plan, count(p.id) FROM accounts a"
                    " LEFT JOIN properties p ON p.account_id = a.id"
                    " WHERE a.slug = :s GROUP BY a.plan"
                ),
                {"s": spec["slug"]},
            ).first()
            if row:
                print(f"{spec['slug']}: plan={row[0]}, {row[1]} properties")
            else:
                print(f"{spec['slug']}: not seeded yet")
    print("\nTrial cleared. Note the Free tester is at its 1-home cap: adding another "
          "restarts the trial.")


def main() -> None:
    engine = create_engine(URL, future=True)
    Session = sessionmaker(bind=engine, future=True)

    with engine.connect() as c:
        if not c.execute(text("SELECT to_regclass('public.accounts')")).scalar():
            sys.exit(f"{DB} has no schema — run `py scripts/dev_setup.py` first")

    if "--reset" in sys.argv:
        reset_plans(engine)
        return

    from mihomes.auth import sessions as sess
    from mihomes.auth.password_identity import create_password_user, find_password_user
    from mihomes.ids import new_id
    from mihomes.tenancy import current_account as current_account_var
    from mihomes.tenancy.connection import bind_account_guc

    cookies = []

    for spec in TESTERS:
        with Session() as s:
            existing = s.execute(
                text("SELECT id FROM accounts WHERE slug = :s"), {"s": spec["slug"]}
            ).scalar()
            if existing:
                print(f"{spec['slug']}: already exists — skipping")
                continue

            # --- user ---------------------------------------------------------------
            user = find_password_user(s, spec["email"])
            if user is None:
                user = create_password_user(
                    s, email=spec["email"], password=PASSWORD, name=spec["name"]
                )
                s.flush()
            # Read now, while attached: `commit()` expires the instance, and touching
            # `user.id` afterwards raises `DetachedInstanceError`.
            user_id = user.id

            # --- account ------------------------------------------------------------
            # Written directly rather than through `create_account_step`, which hardcodes
            # `plan="free"` and would need the plan overwritten immediately after. The
            # billing columns are the webhook's to own (`BILLING` 5-6); there is no service
            # that sets them, because in production Stripe does.
            account_id = new_id()
            s.execute(
                text(
                    "INSERT INTO accounts (id, slug, name, type, plan, subscription_status,"
                    " created_at, updated_at)"
                    " VALUES (:i, :s, :n, 'household', :p, 'active', now(), now())"
                ),
                {"i": account_id, "s": spec["slug"], "n": spec["account"], "p": spec["plan"]},
            )
            s.flush()

            # --- owner membership + content ------------------------------------------
            # Both halves of the tenant binding, per `create_account_step`'s note: the GUC
            # covers the transaction already open (RLS `WITH CHECK` reads
            # `app.current_account`), and the ContextVar covers every later `after_begin`,
            # including the SAVEPOINTs the services open. Either alone leaves a hole.
            token = current_account_var.set(account_id)
            try:
                bind_account_guc(s, account_id)
                s.execute(
                    text(
                        "INSERT INTO memberships (id, account_id, user_id, role, status,"
                        " created_at) VALUES (:i, :a, :u, 'owner', 'active', now())"
                    ),
                    {"i": new_id(), "a": account_id, "u": user_id},
                )
                s.flush()
                summary = seed_content(s, spec["plan"])

                # Inside the binding: `sessions` is tenant-owned too, so the stamp listener
                # reads `current_account` and raises `LookupError` when it is unset.
                raw, _row = sess.create_session(s, user_id=user_id)

                # `current_account_id` is what `resolve_principal` reads; a session without it
                # 403s on every route. `create_session` is called directly here — there is no
                # request for `establish_session` to work from — so it is set explicitly.
                #
                # **In the same transaction as the session row**, not after the commit.
                # `dev_setup.py` does this as a separate `engine.begin()`, and a crash in
                # between leaves a valid-looking cookie bound to no account — measured here on
                # the first run, where the Free tester committed and then the Pro tester raised,
                # stranding Free with `current_account_id IS NULL`.
                s.execute(
                    text("UPDATE sessions SET current_account_id = :a WHERE id = :i"),
                    {"a": account_id, "i": _row.id},
                )
            finally:
                current_account_var.reset(token)

            s.commit()

        print(f"{spec['slug']}: {spec['plan']} plan — {summary}")
        cookies.append((spec, raw))

    if not cookies:
        print("\nnothing to do — both testers already exist")
        return

    print()
    print("=" * 78)
    print("  Sign in with email + password at http://localhost:5000/login")
    print()
    for spec, _raw in cookies:
        print(f"      {spec['email']:<18} {PASSWORD}    ({spec['plan']})")
    print()
    print("  Or paste a cookie into the browser CONSOLE (F12) to skip the form:")
    print()
    for spec, raw in cookies:
        print(f"    # {spec['plan']}")
        print(f'    document.cookie = "{sess.SESSION_COOKIE}={raw}; path=/"; location.reload();')
    print("=" * 78)


if __name__ == "__main__":
    main()
