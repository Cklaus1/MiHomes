"""W.2 · H29 — budget analysis chart JSON must be script-safe.

The chart data is embedded inside a <script> block. If a property/vendor name
contains ``</script>`` (or ``<``/``>``), a raw ``json.dumps`` + ``| safe`` would
let it break out of the script element — stored XSS. Jinja's ``| tojson`` escapes
``<``, ``>``, ``&`` to ``\\u003c`` etc., which is script-safe.
"""

from mihomes.services import budget as budget_svc
from mihomes.services import property as prop_svc


def _seed_hostile_spending(client):
    """Create a property whose name would break out of a <script> block, with spend."""
    from datetime import date

    with client._SessionLocal() as s:
        prop = prop_svc.create_property(s, "Evil</script><script>alert(1)</script>Manor")
        budget_svc.add_transaction(
            s,
            amount=100.0,
            property_id_or_slug=prop.slug,
            category="general",
            tx_date=date.today(),
            description="spend",
        )
        s.commit()


def test_chart_data_does_not_break_out_of_script(client):
    _seed_hostile_spending(client)
    resp = client.get("/budget/?tab=analysis")
    assert resp.status_code == 200
    body = resp.text

    # The hostile property name must NOT appear raw inside the page — a literal
    # </script> in the chart JSON would terminate the <script> element early.
    assert "</script><script>alert(1)</script>" not in body
    # It should instead be present in escaped form somewhere in the chart data.
    assert "\\u003c/script\\u003e" in body or "\\u003cscript\\u003e" in body
