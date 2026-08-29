import { useEffect, useState } from 'react';
import { api } from '../lib/api';

const SKILLS = ['photo', 'drone', 'reel', 'tour', 'twilight'] as const;

type VendorProfile = {
  id: string;
  name: string;
  email: string;
  phone: string;
  avatar_url: string;
  title: string;
  role: string;
  is_owner: boolean;
  tier: number;
  tier_label: string;
  skills: string[];
  rpas_advanced: boolean;
  rpas_certificate_url?: string | null;
  rpas_certificate_level?: string | null;
  service_areas: string[];
  home_city: string;
  max_travel_km: number;
  specialty: string;
  payout_mode: 'owner_full' | 'rate_card';
  payout_note: string;
};

type Settings = {
  company_name: string;
  service_areas: string[];
  support_email: string;
};

type StripeConnectStatus = {
  connected: boolean;
  connect_status: string;
};

function StripeConnectSection({ owner }: { owner: boolean }) {
  const [status, setStatus] = useState<StripeConnectStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');

  const load = () => {
    api.get<StripeConnectStatus>('/api/vendor-os/stripe-connect/status').then(setStatus).catch(() => setStatus(null));
  };

  useEffect(() => {
    load();
    if (new URLSearchParams(window.location.search).get('stripe')) {
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  const connect = async () => {
    setStarting(true);
    setError('');
    try {
      const returnUrl = `${window.location.origin}/settings?stripe=return`;
      const res = await api.post<{ url: string }>('/api/vendor-os/stripe-connect/onboard', { return_url: returnUrl });
      window.location.href = res.url;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start Stripe onboarding');
      setStarting(false);
    }
  };

  return (
    <section className="card full">
      <h3>Payouts</h3>
      <div className="stack">
        {owner && (
          <p className="muted" style={{ padding: 0 }}>
            Owner payout is the full package amount. No platform fee, no rate-card cut.
          </p>
        )}
        {!status && <p className="muted">Loading…</p>}
        {status && (
          <div className="row">
            <span className={`badge ${status.connected ? 'badge-green' : 'badge-gray'}`}>
              {status.connected ? 'Connected' : 'Not connected'}
            </span>
            <span className="muted">
              {status.connected
                ? 'Stripe is connected — job payouts go to this account.'
                : 'Connect a Stripe account to receive job payouts.'}
            </span>
          </div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button type="button" onClick={connect} disabled={starting}>
            {status?.connected ? 'Update Stripe account' : 'Connect Stripe'}
          </button>
          {error && <span className="error">{error}</span>}
        </div>
      </div>
    </section>
  );
}

export function SettingsPage() {
  const [profile, setProfile] = useState<VendorProfile | null>(null);
  const [data, setData] = useState<Settings | null>(null);
  const [serviceAreasRaw, setServiceAreasRaw] = useState('');
  const [areasRaw, setAreasRaw] = useState('');
  const [saved, setSaved] = useState(false);
  const [rpasMsg, setRpasMsg] = useState('');
  const [rpasBusy, setRpasBusy] = useState(false);

  useEffect(() => {
    api.get<VendorProfile>('/api/vendor-os/profile').then((p) => {
      setProfile(p);
      setAreasRaw((p.service_areas || []).join(', '));
    }).catch(() => setProfile(null));
    api.get<Settings>('/api/vendor-os/settings').then((res) => {
      setData(res);
      setServiceAreasRaw(res.service_areas.join(', '));
    }).catch(() => undefined);
  }, []);

  const saveProfile = async () => {
    if (!profile) return;
    const updated = await api.patch<VendorProfile>('/api/vendor-os/profile', {
      name: profile.name,
      phone: profile.phone,
      home_city: profile.home_city,
      max_travel_km: profile.max_travel_km,
      service_areas: areasRaw.split(',').map((v) => v.trim()).filter(Boolean),
      skills: profile.skills,
      specialty: profile.specialty,
    });
    setProfile(updated);
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  const saveCompany = async () => {
    if (!data) return;
    await api.put('/api/vendor-os/settings', {
      ...data,
      service_areas: serviceAreasRaw.split(',').map((v) => v.trim()).filter(Boolean),
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  const onRpas = async (file: File | undefined) => {
    if (!file) return;
    setRpasBusy(true);
    setRpasMsg('');
    try {
      const b64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const text = String(reader.result || '');
          resolve(text.includes(',') ? text.split(',')[1] : text);
        };
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
      const form = new FormData();
      form.append('filename', file.name);
      form.append('file_data', b64);
      const updated = await api.postForm<VendorProfile>('/api/vendor-os/profile/rpas', form);
      setProfile(updated);
      setRpasMsg('Certificate uploaded. Pending verification — not a fake Connected cert.');
    } catch (e) {
      setRpasMsg(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setRpasBusy(false);
    }
  };

  const toggleSkill = (skill: string) => {
    if (!profile || profile.is_owner) return;
    const has = profile.skills.includes(skill);
    setProfile({
      ...profile,
      skills: has ? profile.skills.filter((s) => s !== skill) : [...profile.skills, skill],
    });
  };

  return (
    <div className="page-content">
      <div className="page-header">
        <div>
          <h1>Profile</h1>
          <p>Title, skills, coverage, RPAS, and payouts</p>
        </div>
      </div>

      {profile && (
        <section className="card full">
          <h3>Vendor profile</h3>
          <div className="stack" style={{ padding: 20 }}>
            <div className="row" style={{ padding: 0, alignItems: 'center', gap: 16 }}>
              {profile.avatar_url
                ? <img src={profile.avatar_url} alt="" style={{ width: 56, height: 56, objectFit: 'cover', borderRadius: 2 }} />
                : <div style={{ width: 56, height: 56, borderRadius: 2, background: 'var(--c-bg-deep)', display: 'grid', placeItems: 'center', fontFamily: "'JetBrains Mono', monospace" }}>{(profile.name || '?').slice(0, 2).toUpperCase()}</div>}
              <div>
                <div style={{ fontSize: 18, fontWeight: 500 }}>{profile.name}</div>
                <div className="muted">{profile.title} · Tier {profile.tier_label} ({profile.tier})</div>
              </div>
            </div>
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Name
              <input value={profile.name} onChange={(e) => setProfile({ ...profile, name: e.target.value })} style={{ width: 320 }} />
            </label>
            <p className="muted" style={{ padding: 0 }}>{profile.email}</p>
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Phone
              <input value={profile.phone} onChange={(e) => setProfile({ ...profile, phone: e.target.value })} style={{ width: 320 }} />
            </label>
            <div>
              <p className="muted" style={{ padding: 0, margin: 0 }}>SKILLS</p>
              <div className="row" style={{ padding: '8px 0', gap: 8, flexWrap: 'wrap' }}>
                {SKILLS.map((skill) => (
                  <button
                    key={skill}
                    type="button"
                    className={`status-btn ${profile.skills.includes(skill) ? 'badge-green active' : ''}`}
                    onClick={() => toggleSkill(skill)}
                  >
                    {skill}
                  </button>
                ))}
              </div>
            </div>
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Service area
              <input value={areasRaw} onChange={(e) => setAreasRaw(e.target.value)} placeholder="Mississauga, GTA, Toronto" style={{ width: 480 }} />
            </label>
            <div className="row" style={{ padding: 0 }}>
              <label>
                Home city
                <input value={profile.home_city} onChange={(e) => setProfile({ ...profile, home_city: e.target.value })} />
              </label>
              <label>
                Max travel km
                <input type="number" min={1} value={profile.max_travel_km} onChange={(e) => setProfile({ ...profile, max_travel_km: Number(e.target.value) })} style={{ width: 80 }} />
              </label>
            </div>
            <div>
              <p className="muted" style={{ padding: 0, margin: 0 }}>PAYOUT</p>
              <p style={{ margin: '6px 0 0' }}>{profile.payout_note}</p>
            </div>
            <div>
              <p className="muted" style={{ padding: 0, margin: 0 }}>TRANSPORT CANADA RPAS (ADVANCED)</p>
              <p className="muted" style={{ padding: '6px 0 0' }}>
                {profile.rpas_advanced ? 'Advanced RPAS on file for routing.' : 'Not marked Advanced yet.'}
                {profile.rpas_certificate_url ? ' Certificate uploaded.' : ' Upload the certificate so the document lives on this profile.'}
              </p>
              <input
                type="file"
                accept="application/pdf,image/png,image/jpeg"
                disabled={rpasBusy}
                onChange={(e) => onRpas(e.target.files?.[0])}
                style={{ marginTop: 8 }}
              />
              {rpasMsg && <p className="muted" style={{ padding: '8px 0 0' }}>{rpasMsg}</p>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <button type="button" onClick={saveProfile}>Save profile</button>
              {saved && <span className="muted">Saved</span>}
            </div>
          </div>
        </section>
      )}

      <StripeConnectSection owner={!!profile?.is_owner} />

      {data && (
        <section className="card full">
          <h3>Company</h3>
          <div className="stack">
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Company name
              <input value={data.company_name} onChange={(e) => setData({ ...data, company_name: e.target.value })} style={{ width: 320 }} />
            </label>
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Office service areas
              <input value={serviceAreasRaw} onChange={(e) => setServiceAreasRaw(e.target.value)} placeholder="City, City, …" style={{ width: 480 }} />
            </label>
            <label style={{ flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
              Support email
              <input value={data.support_email} onChange={(e) => setData({ ...data, support_email: e.target.value })} type="email" style={{ width: 320 }} />
            </label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <button type="button" onClick={saveCompany}>Save company</button>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
