"""L11 — POST /properties (create) must return a full page like edit/delete do.

The create route returned a bare `partials/property_list.html` fragment while
the form targets `hx-target="body"`. Swapping a headless fragment into <body>
wiped the nav/chrome, leaving the user on a broken page. Create must return the
same full `properties.html` the edit route returns.
"""


class TestCreatePropertyReturnsFullPage:
    def test_create_returns_full_document(self, client):
        r = client.post(
            "/properties/",
            data={"name": "New Villa", "property_type": "other", "status": "open"},
        )
        assert r.status_code == 200
        body = r.text
        # The new property is listed…
        assert "New Villa" in body
        # …and the page chrome is intact (full document, not a bare fragment).
        assert "<nav" in body
        assert "MiHomes" in body
