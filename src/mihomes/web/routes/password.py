"""Email/password sign-up and sign-in — SPEC-010 §6 Step 3 (A6, A7, A8).

Unauthenticated by design, for the same reason `routes.auth` is: these run *before* an identity
exists, so requiring a declared action would make authentication depend on being authenticated.
`test_route_declarations.py` requires both an allowlist entry and a named mechanism.

**The response to a failed login is identical whether or not the email exists.** Same status,
same body, same redirect — and the same amount of CPU, which is the half that is invisible in
the response and easy to lose. `password_identity.authenticate` is what guarantees the cost;
this module's job is not to leak the difference in the part a user can see.

That constraint is why there is no "no account with that email" message anywhere here, however
much more helpful it would be. It is the natural thing to write and it is the defect.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from mihomes.auth.password_identity import (
    MIN_PASSWORD_LENGTH,
    EmailAlreadyRegistered,
    PasswordTooShort,
    authenticate,
    create_password_user,
)
from mihomes.auth.session_flow import establish_session
from mihomes.auth.sessions import SESSION_COOKIE, lookup_session
from mihomes.web.deps import get_db, templates

router = APIRouter()

#: One string for every failed sign-in, whatever the cause. Wrong password, no such address, an
#: address that only has a Google identity — all three render this. **Naming which it was is the
#: account-existence oracle in prose form**, and it is the single most tempting change to make
#: to this file.
_SIGNIN_FAILED = "That email and password combination is not correct."


@router.get("/signup")
def signup_form(request: Request, db: DbSession = Depends(get_db), error: str = ""):
    """The registration form — and the destination `login.html` has been promising.

    Until now `/signup` did not exist, so the login page could only say "the button above
    creates your account" (c9e2b77): the standard "Create an account" wording would have read
    as a link with nowhere to go. This route is what lets that copy be restored.
    """
    if lookup_session(db, request.cookies.get(SESSION_COOKIE)) is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "signup.html",
        {"error": error, "min_length": MIN_PASSWORD_LENGTH},
    )


@router.post("/signup")
def signup(
    request: Request,
    db: DbSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    name: str = Form(""),
):
    """**A6** — create the user, then go to `/onboarding/`.

    Unlike sign-in, sign-up *does* distinguish its failures: "that email is already registered"
    is information the person in front of the form needs, and they can obtain it anyway by
    trying to register. The asymmetry is deliberate — the login form must not confirm an
    address, the signup form cannot avoid it.
    """
    try:
        user = create_password_user(db, email=email, password=password, name=name)
    except (PasswordTooShort, EmailAlreadyRegistered) as exc:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": str(exc), "min_length": MIN_PASSWORD_LENGTH, "email": email},
            status_code=400,
        )

    # A brand-new user has no membership, so `destination_for` sends them to the wizard. Passed
    # explicitly all the same: signup is the one path where the answer is never `/`, and
    # relying on the lookup would make that a coincidence rather than a guarantee.
    response = establish_session(db, request, user.id, destination="/onboarding/")
    db.commit()
    return response


@router.post("/login")
def login(
    request: Request,
    db: DbSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
):
    """**A7/A8** — verify, rotate the session, route by membership.

    A7: a failure returns the login page with `_SIGNIN_FAILED` and **creates no session**. The
    status code, the body and the cost are the same for an unknown address as for a known one
    with the wrong password.

    A8: on success `establish_session` revokes whatever session the browser arrived with before
    minting a new one. Rotation, not addition — see its docstring.
    """
    user = authenticate(db, email=email, password=password)

    if user is None:
        # No commit and no session. `authenticate` may have re-hashed on a *successful* verify,
        # but this branch is the failure path, so there is nothing to persist.
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": _SIGNIN_FAILED, "email": email, "oauth_configured": _oauth_configured()},
            status_code=401,
        )

    response = establish_session(db, request, user.id)
    db.commit()
    return response


def _oauth_configured() -> bool:
    """Mirrors `routes/auth.py:login` — the template hides the Google button without it.

    Duplicated rather than imported to avoid a cycle between the two route modules. It is two
    environment reads; the thing worth sharing was the session logic, and that is in
    `auth/session_flow.py`.
    """
    import os

    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))
