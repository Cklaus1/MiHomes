"""Playbooks are each account's own, and only editors write them (2026-10-21).

Before: seven markdown files in `knowledge/playbooks/` were listed for every account and nobody
could edit them in the app. User decisions: owners + admins + chosen staff edit; new accounts
start from a generic template gallery. These run through the real app (RLS, the declared-action
gate, the per-member flag), signed in as each role.
"""

from __future__ import annotations

import re
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


def _toggle_tag(html: str, membership_id) -> str:
    import re
    m = re.search(r'<input type="checkbox" name="enabled" id="pb-edit-%s".*?/>' % membership_id,
                  html, re.S)
    assert m, "the playbook toggle is not on the page"
    return " ".join(m.group(0).split())


def test_the_toggle_saves_itself_and_the_page_shows_the_result(web_client_as):
    """The toggle used to need a separate Save button that nothing said to press, so ticking it
    looked like it did nothing. It now submits on change, and the reloaded page must show the new
    state and a message that names what happened (it used to say "Property access saved")."""
    owner = web_client_as("owner")
    staff = web_client_as("staff", scoped_to=[])
    mid = staff.membership_id

    page = owner.get("/settings").text
    tag = _toggle_tag(page, mid)
    assert 'onchange="this.form.requestSubmit()"' in tag
    assert " checked" not in tag
    # The Save button is only a no-script fallback.
    assert "<noscript><button" in page

    on = owner.post(f"/settings/members/{mid}/playbooks", data={"enabled": "on"},
                    follow_redirects=True)
    assert "Playbook editing turned on" in on.text and "Property access saved" not in on.text
    assert " checked" in _toggle_tag(on.text, mid)

    off = owner.post(f"/settings/members/{mid}/playbooks", data={}, follow_redirects=True)
    assert "Playbook editing turned off" in off.text
    assert " checked" not in _toggle_tag(off.text, mid)


def test_staff_cannot_grant_themselves(web_client_as):
    staff = web_client_as("staff", scoped_to=[])
    r = staff.post(f"/settings/members/{staff.membership_id}/playbooks", data={"enabled": "on"})
    assert r.status_code == 403
    assert not pb_svc.can_edit(type("M", (), {"role": "staff", "can_edit_playbooks": False})())


# ── tick boxes (2026-10-22): a playbook is a document, not a task template ───────────────

_KEY = re.compile(r'hx-vals=\'\{"key":"([0-9a-f]{40})"\}\'')


def _make_playbook(owner, content="## Close\n- [ ] Lock the doors\n- [ ] Set the alarm\n", name="Daily Close"):
    owner.post("/playbooks/new", data={"name": name, "content": content})
    slug = name.lower().replace(" ", "-")
    return slug, _KEY.findall(owner.get(f"/playbooks/{slug}").text)


def test_steps_are_real_tick_boxes_and_nothing_creates_tasks(web_client_as):
    owner = web_client_as("owner")
    slug, keys = _make_playbook(owner)
    page = owner.get(f"/playbooks/{slug}").text
    assert len(keys) == 2 and page.count("data-pb-check") >= 2
    assert "0 of 2 done" in page
    assert "Run This Playbook" not in page and "Create 2 Tasks" not in page
    assert owner.post(f"/playbooks/{slug}/run", data={"property_slug": "x"}).status_code in (404, 405)


def test_anyone_who_can_open_a_playbook_can_tick_and_everyone_sees_it(web_client_as):
    owner = web_client_as("owner")
    slug, keys = _make_playbook(owner)
    staff = web_client_as("staff", scoped_to=[])          # no edit grant

    r = staff.post(f"/playbooks/{slug}/check", data={"key": keys[0], "checked": "1"})
    assert r.status_code == 200 and "line-through" in r.text and " checked" in r.text
    assert "data-pb-who" in r.text                         # who ticked it, and when

    page = owner.get(f"/playbooks/{slug}").text            # a different person sees the tick
    assert "1 of 2 done" in page and "data-pb-who" in page

    r = staff.post(f"/playbooks/{slug}/check", data={"key": keys[0]})   # untick: nothing sent
    assert r.status_code == 200 and "line-through" not in r.text
    assert "0 of 2 done" in owner.get(f"/playbooks/{slug}").text


def test_reset_unticks_everything_and_is_for_editors_only(web_client_as):
    owner = web_client_as("owner")
    slug, keys = _make_playbook(owner)
    for k in keys:
        owner.post(f"/playbooks/{slug}/check", data={"key": k, "checked": "1"})
    assert "2 of 2 done" in owner.get(f"/playbooks/{slug}").text

    staff = web_client_as("staff", scoped_to=[])
    assert "Reset all ticks" not in staff.get(f"/playbooks/{slug}").text
    assert staff.post(f"/playbooks/{slug}/reset").status_code == 403
    assert "2 of 2 done" in owner.get(f"/playbooks/{slug}").text        # untouched

    assert "Reset all ticks" in owner.get(f"/playbooks/{slug}").text
    r = owner.post(f"/playbooks/{slug}/reset", follow_redirects=True)
    assert "0 of 2 done" in r.text and "All steps unticked" in r.text


def test_a_staff_member_with_the_edit_grant_can_reset(web_client_as):
    owner = web_client_as("owner")
    slug, keys = _make_playbook(owner)
    owner.post(f"/playbooks/{slug}/check", data={"key": keys[0], "checked": "1"})
    editor = web_client_as("staff", scoped_to=[])
    _grant(web_client_as, editor)
    assert editor.post(f"/playbooks/{slug}/reset", follow_redirects=False).status_code == 303
    assert "0 of 2 done" in owner.get(f"/playbooks/{slug}").text


def test_a_step_changed_under_you_is_refused_not_silently_ticked(web_client_as):
    owner = web_client_as("owner")
    slug, keys = _make_playbook(owner)
    owner.post(f"/playbooks/{slug}/edit",
               data={"name": "Daily Close", "content": "## Close\n- [ ] Lock the doors\n- [ ] Check windows\n"})
    stale = owner.post(f"/playbooks/{slug}/check", data={"key": keys[1], "checked": "1"})
    assert stale.status_code == 409 and "changed" in stale.text
    assert "0 of 2 done" in owner.get(f"/playbooks/{slug}").text


def test_editing_keeps_ticks_on_unchanged_steps(web_client_as):
    owner = web_client_as("owner")
    slug, keys = _make_playbook(owner)
    owner.post(f"/playbooks/{slug}/check", data={"key": keys[0], "checked": "1"})
    owner.post(f"/playbooks/{slug}/edit",
               data={"name": "Daily Close", "content": "## Close\n- [ ] Set the alarm\n- [ ] Lock the doors\n"})
    page = owner.get(f"/playbooks/{slug}").text
    assert "1 of 2 done" in page          # reordered, still ticked


def test_another_accounts_playbook_cannot_be_ticked(web_client_as, account_b):
    web_client_as.connection.execute(
        text("INSERT INTO playbooks (id, account_id, slug, name, content) "
             "VALUES (:id, :a, 'their-list', 'Their List', '- [ ] Private step')"),
        {"id": uuid.uuid4(), "a": account_b},
    )
    owner = web_client_as("owner")
    key = pb_svc.step_keys("- [ ] Private step")[0][0]
    assert owner.post("/playbooks/their-list/check", data={"key": key, "checked": "1"}).status_code == 404


def test_step_text_is_escaped_in_the_tick_boxes(web_client_as):
    owner = web_client_as("owner")
    slug, keys = _make_playbook(owner, content='- [ ] <img src=x onerror=alert(1)> "quoted" **bold**\n', name="Evil")
    page = owner.get(f"/playbooks/{slug}").text
    assert "<img src=x" not in page and "&lt;img" in page and "<strong>bold</strong>" in page
    r = owner.post(f"/playbooks/{slug}/check", data={"key": keys[0], "checked": "1"})
    assert "<img src=x" not in r.text


def test_previews_stay_static(web_client_as):
    owner = web_client_as("owner")
    preview = owner.post("/playbooks/preview", data={"content": "- [ ] Do a thing"}).text
    assert "data-pb-check" not in preview and "<input" not in preview
    tpl = owner.get("/playbooks/templates/emergency-response").text
    assert "data-pb-check" not in tpl
