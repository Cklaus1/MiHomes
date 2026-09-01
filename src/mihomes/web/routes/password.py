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

import logging

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
from mihomes.auth.password_reset import (
    find_user_for_reset,
    issue_reset_token,
    redeem_reset_token,
    verify_reset_token,
)
from mihomes.auth.ratelimit import (
    TooManyAttempts,
    check_login_attempt,
    clear_attempts,
    record_failure,
)
from mihomes.auth.session_flow import establish_session, safe_next
from mihomes.auth.sessions import SESSION_COOKIE, lookup_session
from mihomes.services.email import EmailService, get_email_provider
from mihomes.services.email.provider import EmailProviderError
from mihomes.web.deps import get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter()

#: One string for every failed sign-in, whatever the cause. Wrong password, no such address, an
#: address that only has a Google identity — all three render this. **Naming which it was is the
#: account-existence oracle in prose form**, and it is the single most tempting change to make
#: to this file.
_SIGNIN_FAILED = "That email and password combination is not correct."


@router.get("/signup")
def signup_form(
    request: Request,
    db: DbSession = Depends(get_db),
    error: str = "",
    next: str = "",
):
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
        {"error": error, "min_length": MIN_PASSWORD_LENGTH, "next": safe_next(next) or ""},
    )


@router.post("/signup")
def signup(
    request: Request,
    db: DbSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    name: str = Form(""),
    next: str = Form(""),
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
            {
                "error": str(exc),
                "min_length": MIN_PASSWORD_LENGTH,
                "email": email,
                "next": safe_next(next) or "",
            },
            status_code=400,
        )

    # **A14 — an invitee lands back on their invitation, not in the wizard.** Someone arriving
    # from `/invite/{token}` has an account waiting for them; sending them to `/onboarding/` to
    # create a second one is the wrong destination, and the invitation is then lost.
    #
    # Otherwise: a brand-new user has no membership, so the wizard is right. Passed explicitly
    # rather than left to `destination_for`, because signup is the one path where the answer is
    # never `/` and relying on the lookup would make that a coincidence.
    destination = safe_next(next) or "/onboarding/"
    response = establish_session(db, request, user.id, destination=destination)
    db.commit()
    return response


@router.post("/login")
def login(
    request: Request,
    db: DbSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
):
    """**A7/A8** — verify, rotate the session, route by membership.

    A7: a failure returns the login page with `_SIGNIN_FAILED` and **creates no session**. The
    status code, the body and the cost are the same for an unknown address as for a known one
    with the wrong password.

    A8: on success `establish_session` revokes whatever session the browser arrived with before
    minting a new one. Rotation, not addition — see its docstring.

    **A9/A10 — the throttle, and why it renders the same page.** An exhausted bucket returns
    `_SIGNIN_FAILED` at 401, byte-identical to an ordinary wrong password. Saying "too many
    attempts for that account" would be more helpful and would reopen the oracle A7 closes: an
    attacker could read which addresses exist by throttling each one and watching for the
    message to change.
    """
    ip = request.client.host if request.client else "unknown"

    try:
        # Before `authenticate`, so an exhausted bucket costs no KDF work — the point of a
        # throttle is that a flood becomes cheap to refuse, not merely refused.
        check_login_attempt(db, email=email, ip=ip)
    except TooManyAttempts:
        return _signin_failed(request, email, next)

    user = authenticate(db, email=email, password=password)

    if user is None:
        # **Recorded for every failure, including an address that does not exist** — see
        # `record_failure`. Counting only real addresses would make attempt six differ by
        # whether the account exists, which is A7's oracle wearing a different hat.
        record_failure(db, email=email, ip=ip)
        # No commit and no session. `authenticate` may have re-hashed on a *successful* verify,
        # but this branch is the failure path, so there is nothing to persist.
        return _signin_failed(request, email, next)

    # A10 — the counter resets, or someone who mistyped twice today is locked out by a third
    # mistake next week. Per-email only; see `clear_attempts` for why the IP counter survives.
    clear_attempts(db, email=email)

    # `next` is validated by `safe_next`, so a crafted link cannot turn sign-in into an open
    # redirect. `None` falls through to the usual `/` vs `/onboarding/` choice.
    response = establish_session(db, request, user.id, destination=safe_next(next))
    db.commit()
    return response


def _signin_failed(request: Request, email: str, next: str = ""):
    """The one failure response, shared by the throttle and the wrong-password path.

    Factored out so the two cannot drift. If the throttled branch ever rendered a different
    status, a different message, or a different template, that difference would be the whole
    account-existence oracle — and two copies of a response are how such a difference arrives
    without anyone deciding on it.
    """
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": _SIGNIN_FAILED,
            "email": email,
            "oauth_configured": _oauth_configured(),
            "next": safe_next(next) or "",
        },
        status_code=401,
    )


def _oauth_configured() -> bool:
    """Mirrors `routes/auth.py:login` — the template hides the Google button without it.

    Duplicated rather than imported to avoid a cycle between the two route modules. It is two
    environment reads; the thing worth sharing was the session logic, and that is in
    `auth/session_flow.py`.
    """
    import os

    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


# ── Password reset — SPEC-010 §6 Step 5 (A11, A12, A13) ──────────────────────

#: One string for every outcome of a reset **request**, and the reason is A11: the response for
#: an address with an account must be identical to one without. "We couldn't find that email" is
#: the natural, helpful thing to write and it is a list of which addresses hold accounts.
_RESET_SENT = (
    "If that email has an account with a password, a reset link is on its way. "
    "Check your inbox, and your spam folder."
)

#: One string for every dead link — wrong, used, or expired. `verify_reset_token` collapses the
#: three deliberately; saying "already used" would confirm a token existed.
_RESET_LINK_DEAD = (
    "That reset link is no longer valid. Links expire after an hour and can only be used once."
)


def _public_base(request: Request) -> str:
    """Absolute base for the reset link, taken from the request.

    Derived rather than configured, unlike `landing/routes.py:169`'s `LANDING_BASE_URL`: the
    person receiving this mail just submitted a form on this host, so the host they used is the
    one the link must return them to. An env var would be a second thing to get wrong on every
    deployment, and getting it wrong sends a live credential to the wrong origin.
    """
    return str(request.base_url).rstrip("/")


@router.get("/password/reset")
def reset_request_form(request: Request, error: str = "", sent: bool = False):
    """The "forgot your password" form — the destination `login.html`'s Forgot? link promises."""
    return templates.TemplateResponse(
        request,
        "password_reset_request.html",
        {"error": error, "sent": sent, "message": _RESET_SENT if sent else ""},
    )


@router.post("/password/reset")
def reset_request(
    request: Request,
    db: DbSession = Depends(get_db),
    email: str = Form(...),
):
    """**A11** — mint a token and mail it, and answer identically either way.

    Three ways this route leaks, all of which look like helpfulness:

    1. **A different message for an unknown address.** The obvious one.
    2. **A different response when throttled.** Step 4 wires the limiter into reset as well as
       login (§6), and a throttled reply that differs from an ordinary one re-answers the same
       question. So the throttle returns `_RESET_SENT` too.
    3. **A different amount of work.** Not addressed here and worth naming: minting a token and
       enqueuing mail costs more than doing nothing, so a determined observer could time the
       difference. Closing that would mean doing equivalent fake work for unknown addresses;
       it is not free and the spec does not ask for it. Recorded rather than hidden.

    The response is *always* the same page with the same message. Nothing about it varies.
    """
    ip = request.client.host if request.client else "unknown"

    try:
        # Unthrottled, this route is a mail bomb pointed at any address an attacker names — it
        # sends mail to a stranger on demand. Step 4 said "login and reset"; G4 wired only login.
        check_login_attempt(db, email=email, ip=ip)
    except TooManyAttempts:
        return _reset_requested(request)

    user = find_user_for_reset(db, email)

    if user is not None:
        raw = issue_reset_token(db, user.id)
        db.commit()
        try:
            EmailService(get_email_provider(), session=db).send_password_reset(
                user.email,
                reset_url=f"{_public_base(request)}/password/reset/{raw}",
            )
        except EmailProviderError:
            # **A misconfigured provider must not become an oracle.** `get_email_provider`
            # raises when `RESEND_API_KEY` is unset, and an uncaught raise here is a 500 for a
            # real address and a 200 for an unknown one — which answers the question A11 exists
            # to refuse, and does it on a server that is already broken.
            #
            # Logged, not surfaced: the person on the form cannot act on it, and the operator
            # needs it in the log rather than on a stranger's screen. The token stays minted, so
            # a resend once the key is set will work.
            logger.exception("password reset mail could not be sent to a valid account")
    else:
        # Counted for the unknown case as well, so the throttle engages at the same point for
        # both — the same argument `record_failure`'s docstring makes for login. Counting only
        # real addresses would let an attacker find them by watching where the limit bites.
        record_failure(db, email=email, ip=ip)
        db.commit()

    return _reset_requested(request)


def _reset_requested(request: Request):
    """The single response for every reset request, whatever happened.

    Factored out for the same reason `_signin_failed` is: two copies of a response are how a
    difference between them arrives without anyone deciding on it, and here that difference is
    the whole of A11.
    """
    return templates.TemplateResponse(
        request,
        "password_reset_request.html",
        {"sent": True, "message": _RESET_SENT, "error": ""},
    )


@router.get("/password/reset/{token}")
def reset_form(request: Request, token: str, db: DbSession = Depends(get_db)):
    """The new-password form. **The token is validated before the form renders.**

    Showing the form first and refusing on submit would make someone choose a password, type it
    twice, and only then learn the link was dead — with no way to tell whether the password was
    the problem.
    """
    if verify_reset_token(db, token) is None:
        return templates.TemplateResponse(
            request,
            "password_reset_request.html",
            {"error": _RESET_LINK_DEAD, "sent": False, "message": ""},
            status_code=400,
        )

    return templates.TemplateResponse(
        request,
        "password_reset.html",
        {"token": token, "error": "", "min_length": MIN_PASSWORD_LENGTH},
    )


@router.post("/password/reset/{token}")
def reset_complete(
    request: Request,
    token: str,
    db: DbSession = Depends(get_db),
    password: str = Form(...),
):
    """**A12/A13** — set the password, burn the token, **and revoke every session**.

    A13 is the criterion the spec flags as most likely to be forgotten, and the reason is that
    the feature works perfectly without it: the new password signs you in, and nothing on screen
    says the old sessions are still live. But the person resetting under duress is doing it to
    evict someone, and a reset that leaves the intruder signed in has changed the lock while
    they are still inside.

    `redeem_reset_token` does all three in one transaction — see its docstring for why the order
    matters.
    """
    try:
        user = redeem_reset_token(db, token, password)
    except PasswordTooShort as exc:
        # The token is still live: they typed a short password, not a dead link. Re-render the
        # form rather than the request page, so they can simply try again.
        return templates.TemplateResponse(
            request,
            "password_reset.html",
            {"token": token, "error": str(exc), "min_length": MIN_PASSWORD_LENGTH},
            status_code=400,
        )

    if user is None:
        return templates.TemplateResponse(
            request,
            "password_reset_request.html",
            {"error": _RESET_LINK_DEAD, "sent": False, "message": ""},
            status_code=400,
        )

    # A10's counterpart: someone who reset their password has proved control of the mailbox, so
    # the failures that led them here should not still be counted against them.
    clear_attempts(db, email=user.email)

    # Signed in on the new password immediately. `establish_session` mints a fresh session
    # *after* the revocation above, so the person completing the reset keeps this one and loses
    # every other.
    response = establish_session(db, request, user.id)
    db.commit()
    return response
