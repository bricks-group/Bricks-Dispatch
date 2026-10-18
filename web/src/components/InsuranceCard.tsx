import { FormEvent, useEffect, useRef, useState } from 'react';
import { api, VendorProfile } from '../lib/api';
import {
  insuranceFormNotes,
  insuranceView,
  parseLimit,
  todayIso,
  validateInsuranceForm,
} from '../lib/insurance';

/** Drone liability insurance on Profile. Upload goes to review; only Bricks verifies. */
export function InsuranceCard({ profile, onSaved }: { profile: VendorProfile; onSaved: (p: VendorProfile) => void }) {
  const view = insuranceView(profile.insurance, profile.insurance_review);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [insurer, setInsurer] = useState('');
  const [policy, setPolicy] = useState('');
  const [limit, setLimit] = useState('');
  const [expires, setExpires] = useState('');
  const [aviation, setAviation] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const showForm = view.formOpen || open;
  const isCompany = profile.insurance?.source === 'company';
  const form = { file, limit, expires, aviation };
  const notes = file ? insuranceFormNotes(form) : [];

  // A fixed field clears the last complaint.
  useEffect(() => { setError(''); }, [file, limit, expires, aviation]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const problem = validateInsuranceForm(form);
    if (problem) { setError(problem); return; }
    setBusy(true);
    setError('');
    try {
      const fd = new FormData();
      fd.append('file', file as File);
      fd.append('liability_cad', String(parseLimit(limit)));
      fd.append('expires_on', expires);
      fd.append('aviation_covered', aviation ? 'true' : 'false');
      if (insurer.trim()) fd.append('insurer', insurer.trim());
      if (policy.trim()) fd.append('policy_number', policy.trim());
      const res = await api.postForm<VendorProfile>('/api/vendor-os/profile/insurance', fd);
      onSaved(res);
      setOpen(false);
      setFile(null);
      if (fileRef.current) fileRef.current.value = '';
    } catch (err) {
      setError((err as Error).message || 'Upload failed. Try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card full ins-card">
      <h3>Drone liability insurance</h3>
      <div className="stack">
        <div className={`ins-status ins-${view.tone}`}>
          <span className="ins-dot" aria-hidden="true" />
          <div>
            <p className="ins-head">{view.headline}</p>
            <p className="muted ins-detail">{view.detail}</p>
          </div>
        </div>
        {!showForm && !isCompany && (
          <div>
            <button type="button" onClick={() => setOpen(true)}>{profile.insurance?.valid ? 'Upload a renewal' : 'Upload a new certificate'}</button>
          </div>
        )}

        {showForm && !isCompany && (
          <form className="ins-form" onSubmit={submit} noValidate>
            <label className="ins-field">
              Certificate
              <input ref={fileRef} type="file" accept=".pdf,image/*" onChange={(e) => setFile(e.currentTarget.files?.[0] || null)} />
            </label>
            <div className="ins-row">
              <label className="ins-field">
                Liability limit
                <input inputMode="numeric" placeholder="2,000,000" value={limit} onChange={(e) => setLimit(e.target.value)} />
              </label>
              <label className="ins-field">
                Expires
                <input type="date" min={todayIso()} value={expires} onChange={(e) => setExpires(e.target.value)} />
              </label>
            </div>
            <div className="ins-row">
              <label className="ins-field">
                Insurer
                <input value={insurer} maxLength={120} onChange={(e) => setInsurer(e.target.value)} />
              </label>
              <label className="ins-field">
                Policy number
                <input value={policy} maxLength={80} onChange={(e) => setPolicy(e.target.value)} />
              </label>
            </div>
            <label className="ins-check">
              <input type="checkbox" checked={aviation} onChange={(e) => setAviation(e.target.checked)} />
              Covers drone liability
            </label>
            {notes.map((n) => <p key={n} className="muted ins-detail">{n}</p>)}
            {error && <p className="error ins-error">{error}</p>}
            <div className="ins-actions">
              <button type="submit" className="btn-primary" disabled={busy}>{busy ? 'Uploading…' : 'Send for review'}</button>
              {open && !view.formOpen && <button type="button" className="btn-tertiary" onClick={() => setOpen(false)}>Cancel</button>}
            </div>
          </form>
        )}
      </div>
    </section>
  );
}
