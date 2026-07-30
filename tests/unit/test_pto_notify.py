"""H35 · gateway-aware PTO approver notification.

Before this fix `notify_approver` hardcoded `WhatsAppClient` and only read
`staff.pto_approver_phone` / `owner.whatsapp_phone`. On a Telegram-only install
(no WhatsApp phone, but `telegram.pto_approver_id` set) the approver was never
notified and nothing was logged — a silently dead approval loop.
"""

from unittest.mock import MagicMock, patch

import pytest

from mihomes.models.staff import Staff, StaffRole
from mihomes.services.staff_pto import create_pto_request, notify_approver


@pytest.fixture
def staff(session):
    s = Staff(name="Maria Lopez", slug="maria-lopez", role=StaffRole.HOUSEKEEPER)
    session.add(s)
    session.flush()
    return s


def _config(mapping):
    """Return a get_config stand-in that reads from a dict by key."""
    return lambda session, key, *a, **k: mapping.get(key)


def test_telegram_notified_when_only_chat_id_configured(session, staff):
    req = create_pto_request(session, staff.slug, ["2026-05-01"])
    mock_client = MagicMock()
    cfg = _config({"telegram.pto_approver_id": "99887766"})
    with patch("mihomes.services.config_service.get_config", side_effect=cfg), \
         patch("mihomes.services.gateways.telegram.responder._get_client", return_value=mock_client):
        result = notify_approver(session, req)
    assert result is True
    mock_client.send_message.assert_called_once()
    chat_id, text = mock_client.send_message.call_args[0][:2]
    assert str(chat_id) == "99887766"
    assert f"APPROVE {req.id}" in text


def test_whatsapp_wins_when_phone_configured(session, staff):
    # A phone means a WhatsApp install — must not fall through to Telegram.
    req = create_pto_request(session, staff.slug, ["2026-05-01"])
    wa_client = MagicMock()
    cfg = _config({
        "staff.pto_approver_phone": "+17705550100",
        "telegram.pto_approver_id": "99887766",
    })
    with patch("mihomes.services.config_service.get_config", side_effect=cfg), \
         patch("mihomes.services.gateways.whatsapp.client.WhatsAppClient", return_value=wa_client):
        result = notify_approver(session, req)
    assert result is True
    wa_client.send_message.assert_called_once()
    assert "+17705550100" in wa_client.send_message.call_args[0]


def test_returns_false_when_no_gateway_configured(session, staff):
    req = create_pto_request(session, staff.slug, ["2026-05-01"])
    with patch("mihomes.services.config_service.get_config", side_effect=_config({})):
        assert notify_approver(session, req) is False
