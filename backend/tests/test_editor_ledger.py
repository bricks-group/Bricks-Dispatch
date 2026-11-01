"""Editor cost ledger: Autoenhance cost comes out of Bricks' cut only."""
import asyncio

from app.services import editor_ledger as el


def _job(details="paid www.brxs.ca/book · lead x · stripe cs_live_abc123 · tz America/Toronto", price=300):
    return {"id": "j1", "package_price": price, "rack_price": price, "property_details": details}


def test_live_vs_test_session():
    assert el.is_live_paid_booking(_job())
    assert not el.is_live_paid_booking(_job("stripe cs_test_abc"))
    assert not el.is_live_paid_booking({"property_details": None})


def test_cost_deducts_from_bricks_cut_only(monkeypatch):
    monkeypatch.delenv("AUTOENHANCE_UNIT_COST_CENTS", raising=False)
    ups = [{"provider": "autoenhance"}] * 3 + [{"provider": "bracket_sibling"}] * 6 + [{"provider": "bricks_inhouse"}]
    e = el.editor_cost_entry(_job(), ups, vendor_payout_cents=15000)
    assert e["autoenhance_images"] == 3 and e["inhouse_images"] == 1
    assert e["editor_cost_cents"] == 87
    assert e["client_price_cents"] == 30000 and e["vendor_payout_cents"] == 15000
    assert e["bricks_cut_before_cents"] == 15000
    assert e["bricks_cut_after_cents"] == 15000 - 87
    assert e["deducted_from"] == "bricks_cut"
    assert e["reedit_candidate"] is True and e["test_job"] is False


def test_unit_cost_env(monkeypatch):
    monkeypatch.setenv("AUTOENHANCE_UNIT_COST_CENTS", "75")
    e = el.editor_cost_entry(_job(details="stripe cs_test_x"), [{"provider": "autoenhance"}], None)
    assert e["editor_cost_cents"] == 75 and e["bricks_cut_after_cents"] is None and e["test_job"] is True


def test_prompt_only_once_and_only_live(monkeypatch):
    store, sent = {}, []

    async def get(i):
        return store.get(i)

    async def up(i, *a):
        store[i] = a

    async def notify(*a):
        sent.append(a)

    monkeypatch.setattr(el, "_get_event", get)
    monkeypatch.setattr(el, "_upsert_event", up)
    monkeypatch.setattr(el, "_notify_kam", notify)
    monkeypatch.delenv("AUTOENHANCE_PLAN_ACTIVE", raising=False)
    run = asyncio.run
    assert run(el.maybe_prompt_autoenhance_plan(_job("stripe cs_test_1"))) is False
    assert run(el.maybe_prompt_autoenhance_plan(_job())) is True
    assert run(el.maybe_prompt_autoenhance_plan(_job())) is False
    assert len(sent) == 1 and el.SUBSCRIBE_URL in sent[0][1]
    monkeypatch.setenv("AUTOENHANCE_PLAN_ACTIVE", "true")
    store.clear()
    assert run(el.maybe_prompt_autoenhance_plan(_job())) is False


def test_payout_required():
    from app.routers.stripe_connect_status import payout_account_required
    assert payout_account_required({"id": "0c3fa7c2", "role": "Owner", "title": "Owner", "is_active": True}) is True
    assert payout_account_required({"id": "x", "role": "Photographer", "title": "Photographer", "is_active": True}) is True
    assert payout_account_required(None) is False
