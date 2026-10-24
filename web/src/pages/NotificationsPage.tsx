import { useCallback, useEffect, useState } from 'react';
import { api } from '../lib/api';
import BricksLoader from '../components/BricksLoader';

type Notification = {
  id: string;
  title: string;
  message?: string;
  body?: string;
  type?: string;
  read: boolean;
  created_at: string;
};

function timeAgo(iso: string) {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (!Number.isFinite(diff) || diff < 0) return '';
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function NotificationsPage() {
  const [items, setItems] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError('');
    try {
      const data = await api.get<Notification[] | { items?: Notification[] }>('/api/notifications');
      const rows = Array.isArray(data) ? data : (data.items || []);
      setItems(rows);
    } catch (e) {
      setError((e as Error).message || 'Could not load notifications');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const markRead = async (id: string) => {
    setBusy(true);
    try {
      await api.put(`/api/notifications/${id}/read`);
      setItems((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const markAllRead = async () => {
    setBusy(true);
    try {
      await api.post('/api/notifications/mark-all-read');
      setItems((prev) => prev.map((n) => ({ ...n, read: true })));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const unread = items.filter((n) => !n.read).length;

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Notifications</h1>
          <p>Job offers, uploads, and payout alerts</p>
        </div>
        {unread > 0 ? (
          <button type="button" disabled={busy} onClick={markAllRead}>Mark all read</button>
        ) : null}
      </div>

      {error && <p className="error">{error}</p>}
      {loading && <p className="muted"><BricksLoader variant="assemble" size={40} style={{ display: 'block', marginBottom: 12 }} />Loading…</p>}

      {!loading && items.length === 0 ? (
        <section className="card full">
          <p className="muted" style={{ padding: 20 }}>No notifications yet</p>
        </section>
      ) : null}

      {!loading && items.length > 0 ? (
        <section className="card full">
          <ul className="list">
            {items.map((n) => (
              <li key={n.id} className="list-row" style={{ opacity: n.read ? 0.65 : 1 }}>
                <button
                  type="button"
                  className="row-click"
                  onClick={() => { if (!n.read) markRead(n.id); }}
                  style={{ all: 'unset', cursor: n.read ? 'default' : 'pointer', display: 'block', width: '100%' }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                    <strong>{n.title || 'Update'}</strong>
                    <span className="muted">{timeAgo(n.created_at)}</span>
                  </div>
                  <p className="muted" style={{ margin: '6px 0 0', padding: 0 }}>
                    {n.message || n.body || ''}
                  </p>
                  {!n.read ? <span className="badge badge-attn" style={{ marginTop: 6 }}>Unread</span> : null}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
