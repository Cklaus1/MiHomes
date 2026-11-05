"""Calendar items and alerts open the record they are about (2026-11-05)."""

import html
from datetime import date, timedelta

from mihomes.models.alert import Alert
from mihomes.models.appointment import Appointment
from mihomes.services import consumable as consumable_svc
from mihomes.services import contract as contract_svc
from mihomes.services import issue as issue_svc
from mihomes.services import property as prop_svc
from mihomes.services import vendor as vendor_svc


def _home(client):
    with client._SessionLocal() as s:
        return prop_svc.list_properties(s)[0]


def test_calendar_open_jumps_to_the_items_month_and_opens_it(client):
    home = _home(client)
    later = date.today().replace(day=1) + timedelta(days=95)  # a different month
    with client._SessionLocal() as s:
        appt = Appointment(title="Chimney sweep", property_id=home.id, date=later)
        s.add(appt)
        s.commit()
        aid = appt.id
    page = client.get(f"/calendar/?open=appt-{aid}").text
    assert f'data-open-modal="appt-modal-{aid}"' in page
    assert f'id="appt-modal-{aid}"' in page, "the modal must be on the month that is shown"
    assert later.strftime("%B %Y") in page


def test_calendar_ignores_a_bad_open(client):
    for bad in ("appt-not-a-uuid", "nonsense", "event-00000000-0000-0000-0000-000000000000"):
        r = client.get(f"/calendar/?open={bad}")
        assert r.status_code == 200 and "data-open-modal" not in r.text, bad


def test_dashboard_and_alerts_page_link_to_the_source(client):
    home = _home(client)
    with client._SessionLocal() as s:
        issue = issue_svc.create_issue(s, "Gas smell", str(home.id))
        item = consumable_svc.create_consumable(s, "Salt pellets", str(home.id))
        vendor = vendor_svc.create_vendor(s, "Orkin Test")
        contract = contract_svc.create_contract(s, str(vendor.id), str(home.id), date.today(),
                                                end_date=date.today() + timedelta(days=20))
        s.add_all([
            Alert(alert_type="unresolved_issue", source_entity_type="issue",
                  source_entity_id=issue.id, message="Issue 'Gas smell' unresolved"),
            Alert(alert_type="low_inventory", source_entity_type="consumable",
                  source_entity_id=item.id, message="Salt pellets running low"),
            Alert(alert_type="expiring_contract", source_entity_type="contract",
                  source_entity_id=contract.id, message="Orkin contract expires soon"),
            Alert(alert_type="expiring_insurance", source_entity_type="insurance",
                  source_entity_id=issue.id, message="Policy renews soon"),
        ])
        s.commit()
        expected = (f"/issues/?open={issue.id}", f"/inventory/?open={item.slug}",
                    f"/vendors/?open={vendor.id}")
    for page in (html.unescape(client.get("/").text), html.unescape(client.get("/alerts/").text)):
        for url in expected:
            assert f'href="{url}"' in page, url
    alerts_page = client.get("/alerts/").text
    assert "Policy renews soon</div>" in alerts_page, "no page for insurance: plain text, no link"


def test_inventory_page_accepts_open(client):
    home = _home(client)
    with client._SessionLocal() as s:
        item = consumable_svc.create_consumable(s, "Dish tabs", str(home.id))
        s.commit()
        slug = item.slug
    page = client.get(f"/inventory/?open={slug}").text
    assert f'data-slug="{slug}"' in page and "params.get('open')" in page
