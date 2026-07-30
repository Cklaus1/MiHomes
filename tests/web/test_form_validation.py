"""W.7 · M16 — budget POST amounts parsed via parse_money, not raw Form(float).

FastAPI's native ``amount: float = Form(...)`` rejects a non-numeric or
``$1,000``-formatted value with a bare 422 error page. Routing through
``parse_money`` accepts currency formatting and returns a friendly form error.
"""

from mihomes.services import budget as budget_svc


def _prop_id(client):
    from mihomes.services import property as prop_svc

    with client._SessionLocal() as s:
        # The seeded "Test Manor" from the shared fixture.
        p = prop_svc.list_properties(s)[0]
        return p.id


def test_transaction_accepts_currency_formatting(client):
    pid = _prop_id(client)
    resp = client.post(
        "/budget/transactions",
        data={
            "property_id": str(pid),
            "description": "Formatted",
            "amount": "$1,234.50",
            "category": "general",
        },
    )
    assert resp.status_code == 200
    with client._SessionLocal() as s:
        txns = budget_svc.list_transactions(s)
        assert any(round(t.amount, 2) == 1234.50 for t in txns)


def test_transaction_bad_amount_surfaces_form_error(client):
    pid = _prop_id(client)
    resp = client.post(
        "/budget/transactions",
        data={
            "property_id": str(pid),
            "description": "Bad",
            "amount": "not-a-number",
            "category": "general",
        },
    )
    # Friendly 400, not FastAPI's 422 coercion page and not a 500.
    assert resp.status_code == 400
    assert "number" in resp.text.lower()


def test_set_budget_bad_amount_surfaces_form_error(client):
    pid = _prop_id(client)
    resp = client.post(
        "/budget/set",
        data={
            "property_id": str(pid),
            "category": "general",
            "period": "monthly",
            "amount": "abc",
        },
    )
    assert resp.status_code == 400
    assert "number" in resp.text.lower()
