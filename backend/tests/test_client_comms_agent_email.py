"""www /book agent emails use jobs.agent_email (not Companion bookings id)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_booking_confirmed_prefers_agent_email():
    src = (ROOT / "app/services/client_comms.py").read_text()
    fn = src.split("async def notify_booking_confirmed")[1].split("async def notify_photographer_en_route")[0]
    assert "agent_email" in fn
    assert "_email_agent_direct" in fn
    assert "Shoot confirmed" in fn
    assert "_call_booking_comms" in fn  # Companion fallback
    # Must not require bookings row when agent_email present
    assert 'if agent_email:' in fn
    assert "booking_id" in fn


def test_en_route_prefers_agent_email():
    src = (ROOT / "app/services/client_comms.py").read_text()
    fn = src.split("async def notify_photographer_en_route")[1].split("async def notify_capture_delivered")[0]
    assert "agent_email" in fn
    assert "_email_agent_direct" in fn
    assert "_call_booking_comms" in fn


def test_capture_delivered_prefers_agent_email():
    src = (ROOT / "app/services/client_comms.py").read_text()
    fn = src.split("async def notify_capture_delivered")[1]
    assert "agent_email" in fn
    assert "_email_agent_direct" in fn
    assert "Media ready" in fn
    assert "/api/internal/comms/job-delivered" in fn


def test_agent_emails_deep_link_listing_file():
    src = (ROOT / "app/services/client_comms.py").read_text()
    assert "def _listing_desk_url" in src
    confirm = src.split("async def notify_booking_confirmed")[1].split("async def notify_photographer_en_route")[0]
    deliver = src.split("async def notify_capture_delivered")[1]
    assert "_listing_desk_url(job_id)" in confirm
    assert "_listing_desk_url(job_id)" in deliver
    assert "Copy client link" in deliver
    # Generic /properties alone is not enough for first-booking desk open
    assert '{_OS_DESK_URL}/properties"' not in confirm
