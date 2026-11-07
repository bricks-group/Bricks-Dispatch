"""Security contract for every calendar provider, without network or account access."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import calendar_oauth_state as state, calendar_url_guard as guard
from app.services import vendor_calendars as calendars
from test_calendar_oauth_security import NonceDb


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv('CALENDAR_OAUTH_STATE_SECRET', 'unit-test-only')
    monkeypatch.setenv('GOOGLE_CALENDAR_CONNECT_VIA', 'direct')
    for provider in ('GOOGLE', 'MICROSOFT'):
        monkeypatch.setenv(f'{provider}_OAUTH_CLIENT_ID', 'test-client')
        monkeypatch.setenv(f'{provider}_OAUTH_CLIENT_SECRET', 'test-secret')
        monkeypatch.setenv(f'{provider}_OAUTH_REDIRECT_URI',
                           f'https://api.example.com/api/vendor-os/calendars/{provider.lower()}/callback')


@pytest.mark.parametrize('provider', ['google', 'microsoft'])
def test_authenticated_connect_mints_signed_state_and_start_binds_browser(provider, monkeypatch):
    from app.routers import vendor_os as router
    monkeypatch.setattr(router, 'resolve_vendor_actor_id', AsyncMock(return_value='member-a'))
    monkeypatch.setattr(router, 'get_vendor_member', AsyncMock(return_value={'id': 'member-a', 'vendor_id': 'v'}))
    monkeypatch.setattr(router, 'get_db', AsyncMock(return_value=NonceDb()))
    # Google defaults to Supabase; explicitly exercise the direct OAuth path.
    monkeypatch.setattr(calendars, 'google_connect_via', lambda: 'direct')
    result = asyncio.run(router.connect_vendor_calendar(provider, payload={}, authorization='Bearer fixture', x_user_id=None))
    url = result['oauth_url']
    assert urlsplit(url).path.endswith(f'/{provider}/start')
    signed = parse_qs(urlsplit(url).query)['state'][0]
    assert state.read_state(signed, provider)['member_id'] == 'member-a'
    response = asyncio.run(getattr(router, f'{provider}_calendar_oauth_start')(state=signed))
    assert parse_qs(urlsplit(response.headers['location']).query)['state'] == [signed]
    cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'secure' in cookie and 'samesite=lax' in cookie
    other = 'microsoft' if provider == 'google' else 'google'
    wrong = asyncio.run(getattr(router, f'{other}_calendar_oauth_start')(state=signed))
    assert 'calendar_error' in wrong.headers['location']


@pytest.mark.parametrize('provider', ['google', 'microsoft'])
@pytest.mark.parametrize('bad', ['unsigned', 'wrong-cookie', 'expired', 'wrong-provider', 'db-outage', 'replay'])
def test_callbacks_reject_invalid_state_before_exchange(provider, bad, monkeypatch):
    from app.routers import vendor_os as router
    db = NonceDb(fail=bad == 'db-outage')
    token = state.mint_state('member-a', provider, **({'now': 1} if bad == 'expired' else {}))
    nonce = state.read_state(token, provider, **({'now': 1} if bad == 'expired' else {}))['nonce']
    if bad == 'unsigned': token = 'member-a'
    if bad == 'wrong-cookie': nonce = 'another-browser'
    if bad == 'wrong-provider': token = state.mint_state('member-a', 'microsoft' if provider == 'google' else 'google')
    if bad == 'replay':
        asyncio.run(state.consume_state(token, provider, cookie_nonce=nonce, db=db))
    exchange = AsyncMock()
    monkeypatch.setattr(calendars, f'exchange_{provider}_code', exchange)
    monkeypatch.setattr(router, 'get_db', AsyncMock(return_value=db))
    result = asyncio.run(getattr(router, f'{provider}_calendar_oauth_callback')(
        SimpleNamespace(cookies={state.COOKIE_NAME: nonce}), code='fixture', state=token))
    assert 'calendar_error' in result.headers['location']
    exchange.assert_not_awaited()


@pytest.mark.parametrize('provider', ['google', 'microsoft'])
def test_callback_success_and_single_use(provider, monkeypatch):
    from app.routers import vendor_os as router
    token = state.mint_state('member-a', provider)
    nonce = state.read_state(token, provider)['nonce']
    exchange = AsyncMock(return_value={'access_token': 'fixture', 'refresh_token': 'fixture'})
    persist = AsyncMock(return_value={'connected': True})
    monkeypatch.setattr(calendars, f'exchange_{provider}_code', exchange)
    monkeypatch.setattr(calendars, f'persist_{provider}_oauth_if_verified', persist)
    monkeypatch.setattr(router, 'get_db', AsyncMock(return_value=NonceDb()))
    callback = getattr(router, f'{provider}_calendar_oauth_callback')
    request = SimpleNamespace(cookies={state.COOKIE_NAME: nonce})
    first = asyncio.run(callback(request, code='fixture', state=token))
    second = asyncio.run(callback(request, code='fixture', state=token))
    assert f'calendar_connected={provider}' in first.headers['location']
    assert 'calendar_error' in second.headers['location']
    assert exchange.await_count == persist.await_count == 1
    assert persist.await_args.kwargs['member_id'] == 'member-a'


@pytest.mark.parametrize('provider', ['apple', 'ics'])
@pytest.mark.parametrize('phase', ['connect', 'availability'])
@pytest.mark.parametrize('address', ['127.0.0.1', '169.254.169.254', '::1', '10.0.0.1'])
def test_supplied_urls_cannot_reach_private_network(provider, phase, address, monkeypatch):
    monkeypatch.setattr(guard, '_default_resolver', lambda *_: [address])
    network = MagicMock(side_effect=AssertionError('Network must not be reached'))
    monkeypatch.setattr(guard.httpx, 'AsyncClient', network)
    now = datetime.now(timezone.utc)
    url = 'https://calendar.example.com/feed'
    if phase == 'connect':
        result = asyncio.run(calendars.apple_caldav_ping(url, 'fixture', 'user') if provider == 'apple'
                             else calendars.ics_feed_ping(url))
        assert not result['ok']
    else:
        result = asyncio.run(calendars._apple_caldav_busy(url, 'fixture', 'user', now, now + timedelta(hours=1))
                             if provider == 'apple' else calendars._ics_busy(url, now, now + timedelta(hours=1)))
        assert result is None
    network.assert_not_called()


@pytest.mark.parametrize('provider', ['apple', 'ics'])
@pytest.mark.parametrize('url', ['http://public.example.com/calendar', 'https://127.0.0.1/calendar',
                               'https://localhost/calendar', 'https://u:p@public.example.com/calendar'])
def test_save_rejects_unsafe_url_before_probe_or_persistence(provider, url, monkeypatch):
    probe = AsyncMock(return_value={'ok': True})
    monkeypatch.setattr(calendars, 'apple_caldav_ping' if provider == 'apple' else 'ics_feed_ping', probe)
    db = MagicMock()
    fn = calendars.persist_apple_caldav_if_verified if provider == 'apple' else calendars.persist_ics_if_verified
    args = {'caldav_url': url, 'password': 'fixture', 'username': 'user'} if provider == 'apple' else {'ics_url': url}
    result = asyncio.run(fn(db, member_id='member-a', vendor_id='v', **args))
    assert result['status'] == 'invalid_url' and not result['connected']
    probe.assert_not_awaited()
    db.table.assert_not_called()


@pytest.mark.parametrize('provider', ['apple', 'ics'])
def test_connect_probe_uses_guarded_fetch(provider, monkeypatch):
    fetch = AsyncMock(return_value=(207 if provider == 'apple' else 200, 'BEGIN:VCALENDAR\nEND:VCALENDAR'))
    monkeypatch.setattr(guard, 'guarded_request', fetch)
    result = asyncio.run(calendars.apple_caldav_ping('https://calendar.example.com/', 'fixture', 'user')
                         if provider == 'apple' else calendars.ics_feed_ping('https://calendar.example.com/'))
    assert result['ok']
    assert fetch.await_args.args[0] == ('REPORT' if provider == 'apple' else 'GET')
    if provider == 'apple': assert fetch.await_args.kwargs['auth'] == ('user', 'fixture')
