"""Naming cleanup (2026-11-05): one name per thing, and search results that open the thing."""

import html

from mihomes.models.asset import AssetType
from mihomes.services import asset as asset_svc
from mihomes.services import note as note_svc
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services import task as task_svc


def _home(client):
    with client._SessionLocal() as s:
        return prop_svc.list_properties(s)[0]


def test_sidebar_names(client):
    page = html.unescape(client.get("/").text)
    assert ">Task templates<" in page.replace("\n", "").replace("  ", "") or "Task templates" in page
    assert "Rooms & assets" in page
    assert ">Templates<" not in page.replace(" ", "").replace("\n", "")


def test_task_templates_page_says_what_it_is(client):
    page = client.get("/templates/").text
    assert "<title>Task templates — MiHomes</title>" in page


def test_messenger_log_is_named_and_linked_from_settings(client):
    log = client.get("/conversations/").text
    assert "Messenger log" in log and "For in-app messaging, use Chat." in log
    assert 'href="/conversations/"' in client.get("/settings/connections").text


def test_search_labels_match_the_sidebar(client):
    """The sidebar calls /staff/ "Directory"; search used to head the same people "People"."""
    from mihomes.services import staff as staff_svc

    with client._SessionLocal() as s:
        staff_svc.create_staff(s, "Zebedee Quist")
        s.commit()
    page = client.get("/search/?q=Zebedee").text
    heading = '<span class="text-sm font-semibold text-gray-700">Directory</span>'
    assert heading in page
    assert '<span class="text-sm font-semibold text-gray-700">People</span>' not in page


def test_asset_result_opens_the_asset_in_its_room(client):
    home = _home(client)
    with client._SessionLocal() as s:
        room = space_svc.create_space(s, "Boot Room", str(home.id))
        boiler = asset_svc.create_asset(s, "Quokka boiler", AssetType.APPLIANCE, str(home.id),
                                        space_id_or_slug=str(room.id))
        loose = asset_svc.create_asset(s, "Quokka ladder", AssetType.EQUIPMENT, str(home.id))
        s.commit()
        urls = (f"/assets/{home.slug}/{room.slug}?open=asset-{boiler.id}",
                f"/assets/{home.slug}/unassigned?open=asset-{loose.id}")
    page = html.unescape(client.get("/search/?q=Quokka").text)
    for url in urls:
        assert f'href="{url}"' in page, url


def test_note_result_opens_the_record_it_is_on(client):
    home = _home(client)
    with client._SessionLocal() as s:
        task = task_svc.create_task(s, "Descale kettle", str(home.id))
        note_svc.add_note(s, f"task:{task.id}", "wombat-shaped limescale")
        s.commit()
        tid = task.id
    page = html.unescape(client.get("/search/?q=wombat").text)
    assert f'href="/tasks/?open={tid}"' in page
    assert "Note on a task: Descale kettle" in page
