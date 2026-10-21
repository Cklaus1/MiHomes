"""Playbooks are each account's own, and only editors write them (2026-10-21).

Before: seven markdown files in `knowledge/playbooks/` were listed for every account and nobody
could edit them in the app. User decisions: owners + admins + chosen staff edit; new accounts
start from a generic template gallery. These run through the real app (RLS, the declared-action
gate, the per-member flag), signed in as each role.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from mihomes.services import playbook as pb_svc


def _grant(web_client_as, client, on: bool = True):
    web_client_as.connection.execute(
        text("UPDATE memberships SET can_edit_playbooks = :on WHERE id = :m"),
        {"on": on, "m": client.membership_id},
    )


def test_admin_creates_edits_and_deletes(web_client_as):
    admin = web_client_as("admin")
    r = admin.post("/playbooks/new", data={"name": "Guest Arrival", "content": "## Before\n- [ ] Air out rooms\n"},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/playbooks/guest-arrival?notice=created"

    page = admin.get("/playbooks/guest-arrival").text
    assert 'href="/playbooks/guest-arrival/edit"' in page and "Air out rooms" in page

    r = admin.post("/playbooks/guest-arrival/edit",
                   data={"name": "Guest Arrival", "content": "- [ ] Air out rooms\n- [ ] Stock towels\n"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert "Stock towels" in admin.get("/playbooks/guest-arrival").text

    r = admin.post("/playbooks/guest-arrival/delete", follow_redirects=False)
    assert r.headers["location"] == "/playbooks/?notice=deleted"
    assert "Guest Arrival" not in admin.get("/playbooks/").text


def test_staff_without_the_grant_can_view_but_not_write(web_client_as):
    owner = web_client_as("owner")
    owner.post("/playbooks/new", data={"name": "House Rules", "content": "- [ ] Read them\n"})

    staff = web_client_as("staff", scoped_to=[])
    listing = staff.get("/playbooks/").text
    assert "House Rules" in listing
    assert 'href="/playbooks/new"' not in listing
    assert 'href="/playbooks/house-rules/edit"' not in staff.get("/playbooks/house-rules").text

    assert staff.post("/playbooks/new", data={"name": "Sneaky", "content": ""}).status_code == 403
    assert staff.post("/playbooks/house-rules/edit",
                      data={"name": "Hacked", "content": ""}).status_code == 403
    assert staff.post("/playbooks/house-rules/delete").status_code == 403
    assert staff.post("/playbooks/templates/hiring/use").status_code == 403
    assert "House Rules" in owner.get("/playbooks/").text  # untouched


def test_staff_with_the_grant_can_write(web_client_as):
    staff = web_client_as("staff", scoped_to=[])
    _grant(web_client_as, staff)
    assert 'href="/playbooks/new"' in staff.get("/playbooks/").text
    r = staff.post("/playbooks/new", data={"name": "Linen Rotation", "content": "- [ ] Swap sheets\n"},
                   follow_redirects=False)
    assert r.status_code == 303

    _grant(web_client_as, staff, on=False)  # withdrawn takes effect on the next request
    assert staff.post("/playbooks/linen-rotation/delete").status_code == 403


def test_template_gallery_copies_into_the_account(web_client_as):
    owner = web_client_as("owner")
    gallery = owner.get("/playbooks/templates").text
    assert "Use this template" in gallery and "Emergency" in gallery
    r = owner.post("/playbooks/templates/emergency-response/use", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/edit?notice=copied")
    slug = r.headers["location"].split("/")[2]
    assert owner.get(f"/playbooks/{slug}").status_code == 200


def test_another_accounts_playbooks_are_invisible(web_client_as, account_b):
    other_id = uuid.uuid4()
    web_client_as.connection.execute(
        text("INSERT INTO playbooks (id, account_id, slug, name, content) "
             "VALUES (:id, :a, 'secret-hiring', 'Secret Hiring', '- [ ] Private step')"),
        {"id": other_id, "a": account_b},
    )
    owner = web_client_as("owner")
    assert "Secret Hiring" not in owner.get("/playbooks/").text
    r = owner.get("/playbooks/secret-hiring", follow_redirects=False)
    assert r.status_code in (302, 303, 307) and "Private step" not in r.text


def test_playbook_content_is_escaped(web_client_as):
    owner = web_client_as("owner")
    owner.post("/playbooks/new", data={
        "name": "Escapes",
        "content": "## <img src=x onerror=alert(1)>\n- [ ] <script>alert(1)</script> **bold**\n",
    })
    page = owner.get("/playbooks/escapes").text
    assert "<script>alert(1)</script>" not in page
    assert "<img src=x" not in page
    assert "&lt;script&gt;" in page and "<strong>bold</strong>" in page

    preview = owner.post("/playbooks/preview", data={"content": "- [ ] <b onmouseover=x>hi</b>"}).text
    assert "<b onmouseover" not in preview


def test_settings_toggle_grants_and_is_staff_only(web_client_as):
    owner = web_client_as("owner")
    staff = web_client_as("staff", scoped_to=[])
    r = owner.post(f"/settings/members/{staff.membership_id}/playbooks", data={"enabled": "on"},
                   follow_redirects=False)
    assert r.status_code == 303
    flag = web_client_as.connection.execute(
        text("SELECT can_edit_playbooks FROM memberships WHERE id = :m"), {"m": staff.membership_id}
    ).scalar()
    assert flag is True
    assert "Can create and edit playbooks" in owner.get("/settings").text

    admin = web_client_as("admin")
    r = owner.post(f"/settings/members/{admin.membership_id}/playbooks", data={"enabled": "on"})
    assert "only for staff" in r.text  # refused: admins can always edit


def test_staff_cannot_grant_themselves(web_client_as):
    staff = web_client_as("staff", scoped_to=[])
    r = staff.post(f"/settings/members/{staff.membership_id}/playbooks", data={"enabled": "on"})
    assert r.status_code == 403
    assert not pb_svc.can_edit(type("M", (), {"role": "staff", "can_edit_playbooks": False})())
