"""Import from a spreadsheet: the /import pages end to end (2026-11-05)."""

import html

from sqlalchemy import text

from mihomes.models.property import Property
from mihomes.models.staff import Staff
from mihomes.models.task import Task
from mihomes.services import property as prop_svc
from mihomes.services import staff as staff_svc


def _preview(client, entity_type, body: bytes, name="data.csv"):
    return client.post("/import/preview", data={"entity_type": entity_type},
                       files={"file": (name, body, "text/csv")})


def _apply(client, entity_type, csv_text: str):
    return client.post("/import/apply", data={"entity_type": entity_type, "csv_text": csv_text})


def _count(client, model, **where):
    with client._SessionLocal() as s:
        q = s.query(model)
        for k, v in where.items():
            q = q.filter(getattr(model, k) == v)
        return q.count()


def _home(client):
    with client._SessionLocal() as s:
        return prop_svc.list_properties(s)[0]


def test_page_and_template(client):
    page = client.get("/import/?type=task")
    assert page.status_code == 200
    assert "Columns for tasks" in page.text and 'href="/import/template/task.csv"' in page.text
    tpl = client.get("/import/template/task.csv")
    assert tpl.status_code == 200 and tpl.headers["content-type"].startswith("text/csv")
    assert tpl.text.startswith("title,property,priority,due_date,assignee")
    assert client.get("/import/template/nope.csv").status_code == 404


def test_preview_saves_nothing_and_understands_a_real_spreadsheet(client):
    home = _home(client)
    with client._SessionLocal() as s:
        staff_svc.create_staff(s, "Rosa Diaz")
        s.commit()
    before = _count(client, Task)
    # Excel's UTF-8 BOM, "Title Case" headers, the home and the assignee by name, a US date.
    body = ("﻿Title,Property,Priority,Due Date,Assignee\r\n"
            f"Clean gutters,{home.name},High,11/30/2026,rosa diaz\r\n"
            f"Service boiler,{home.slug},,2026-12-01,\r\n").encode("utf-8")
    r = _preview(client, "task", body)
    assert r.status_code == 200
    assert "nothing has been saved yet" in r.text
    assert r.text.count(">Ready<") == 2, r.text
    assert "Import 2 tasks" in r.text
    assert _count(client, Task) == before, "a preview must not create anything"


def test_import_then_reupload_skips_what_is_already_there(client):
    home = _home(client)
    csv_text = (f"title,property,priority\r\nWinterize pipes,{home.name},urgent\r\n"
                f"Check smoke alarms,{home.name},high\r\n")
    r = _apply(client, "task", csv_text)
    assert "Import finished" in r.text and "<strong>2</strong> tasks added" in r.text
    with client._SessionLocal() as s:
        task = s.query(Task).filter(Task.title == "Winterize pipes").one()
        assert task.property_id == home.id and task.priority.value == "urgent"

    again = _apply(client, "task", csv_text)
    assert "<strong>0</strong> tasks added" in again.text and "2 already there" in again.text
    assert _count(client, Task, title="Winterize pipes") == 1


def test_an_ambiguous_home_name_is_refused_not_guessed(client):
    with client._SessionLocal() as s:
        prop_svc.create_property(s, "Twin House")
        prop_svc.create_property(s, "Twin House")
        s.commit()
    r = _apply(client, "task", "title,property\r\nPaint shed,Twin House\r\n")
    assert "2 homes are called “Twin House”. Use the slug instead" in r.text
    assert _count(client, Task, title="Paint shed") == 0


def test_one_bad_row_does_not_stop_the_rest(client):

    home = _home(client)
    r = _apply(client, "task", (
        "title,property,priority,due_date\r\n"
        f"Good one,{home.name},low,\r\n"
        "Lost one,Nowhere Manor,,\r\n"
        f"Odd priority,{home.name},whenever,\r\n"
        f"Odd date,{home.name},,next tuesday\r\n"
        f",{home.name},,\r\n"))
    page = html.unescape(r.text)
    assert "<strong>1</strong> tasks added" in page and "4 not imported" in page
    assert "No home called “Nowhere Manor”." in page
    assert "“whenever” isn't a priority" in page
    assert "“next tuesday” isn't a date" in page
    assert "Missing title." in page
    assert _count(client, Task, title="Good one") == 1


def test_semicolons_and_windows_1252(client):
    body = "name;role;phone\r\nJosé Núñez;Groundskeeper;555-0100\r\n".encode("cp1252")
    preview = _preview(client, "staff", body)
    assert ">Ready<" in preview.text and "José Núñez" in preview.text
    _apply(client, "staff", "name;role;phone\r\nJosé Núñez;Groundskeeper;555-0100\r\n")
    with client._SessionLocal() as s:
        person = s.query(Staff).filter(Staff.name == "José Núñez").one()
        assert person.role.value == "groundskeeper"


def test_vendors_with_several_categories(client):
    r = _apply(client, "vendor", "company,contact,service_categories\r\n"
                                 "Acme Plumbing,Ann,plumbing;hvac\r\n")
    assert "<strong>1</strong> vendors added" in r.text


def test_free_plan_home_limit_applies_to_imports(client, account_a):
    with client._SessionLocal() as s:
        s.execute(text("UPDATE accounts SET plan = 'free' WHERE id = :id"), {"id": account_a})
        s.commit()
    before = _count(client, Property)
    r = _apply(client, "property", "name,address\r\nBeach House,1 Ocean Dr\r\n")
    assert "<strong>0</strong> homes added" in r.text and "1 not imported" in r.text
    assert _count(client, Property) == before


def test_unknown_columns_are_named_and_empty_files_explained(client):
    home = _home(client)
    r = _preview(client, "issue", f"title,property,colour\r\nLeak,{home.name},blue\r\n".encode())
    assert "Ignored columns" in r.text and "colour" in r.text
    empty = _preview(client, "issue", b"title,property\r\n")
    assert "The file has no rows under its header line." in empty.text


def test_settings_and_dashboard_link_to_import(client):
    assert 'href="/import/"' in client.get("/settings").text


# ── Export (2026-11-05) ────────────────────────────────────────────────────────────────────


def _export(client, entity_type):
    r = client.get(f"/export/{entity_type}.csv")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    return r.content.decode("utf-8")


def test_export_uses_the_import_columns_and_names(client):
    home = _home(client)
    _apply(client, "task", f"title,property,priority,due_date\r\nOil the gate,{home.name},high,2026-12-01\r\n")
    body = _export(client, "task")
    assert body.startswith("﻿title,property,priority,due_date,assignee,repeat,category,"
                           "description,estimated_hours,status\r\n")
    assert f"Oil the gate,{home.name},high,2026-12-01,,once" in body
    assert client.get("/export/nope.csv").status_code == 404


def test_tasks_under_same_named_homes_export_the_home_slug(client):
    """A home's own `name` column stays its name; a task's `property` reference uses the slug
    when the name is shared, so re-importing files the task under the right home."""
    from mihomes.services import task as task_svc

    with client._SessionLocal() as s:
        a = prop_svc.create_property(s, "Twin House")
        b = prop_svc.create_property(s, "Twin House")
        task_svc.create_task(s, "Sweep porch A", str(a.id))
        task_svc.create_task(s, "Sweep porch B", str(b.id))
        s.commit()
        slug_a, slug_b = a.slug, b.slug
    body = _export(client, "task")
    assert f"Sweep porch A,{slug_a}," in body and f"Sweep porch B,{slug_b}," in body
    assert body.count("Twin House") == 0


def test_round_trip_reimports_as_already_there(client):
    home = _home(client)
    _apply(client, "issue", f"title,property,severity\r\nCracked tile,{home.name},low\r\n")
    exported = _export(client, "issue").lstrip("﻿")
    again = _apply(client, "issue", exported)
    assert "<strong>0</strong> issues added" in again.text and "Ignored columns" not in again.text
    assert "already there" in again.text


def test_formulas_are_defused_on_the_way_out_and_restored_on_the_way_in(client):
    home = _home(client)
    hostile = '=HYPERLINK("http://evil.example","click")'
    with client._SessionLocal() as s:
        from mihomes.services import issue as issue_svc

        issue_svc.create_issue(s, "Strange message", str(home.id), description=hostile)
        s.commit()
    body = _export(client, "issue")
    assert "'=HYPERLINK" in body, "a cell starting with = must not run as a formula in Excel"

    from mihomes.services.csv_io import _unguard

    assert _unguard("'" + hostile) == hostile and _unguard("'plain") == "'plain"


def test_list_pages_offer_export_to_owners(client):
    for path, kind in (("/properties", "property"), ("/staff/", "staff"), ("/vendors/", "vendor"),
                       ("/tasks/", "task"), ("/issues/", "issue")):
        html = client.get(path, follow_redirects=True).text
        assert f'href="/export/{kind}.csv"' in html, path
