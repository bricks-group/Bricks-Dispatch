import { useCallback, useState } from 'react';
import { LegalDocSheet } from '../components/legal/LegalDocSheet';
import { acceptTerms } from '../lib/api';
import { LEGAL_DOCS, REQUIRED_DOCS, allRead, lockedReason, type LegalKey, type LoadedDoc } from '../lib/legal-docs';

/**
 * Members an owner added from the team screen reach GO without having accepted
 * the contractor agreement or privacy policy (applicants do it on www). They
 * read both here once, each scrolled to the end, before the app opens.
 */
export function AgreeTermsPage({ name, onAccepted, onLogout }: { name?: string; onAccepted: () => void; onLogout: () => void }) {
  const [read, setRead] = useState<Partial<Record<LegalKey, boolean>>>({});
  const [open, setOpen] = useState<LegalKey | null>(null);
  const [icVersion, setIcVersion] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const done = allRead(REQUIRED_DOCS, read) && !!icVersion;
  const markRead = useCallback((k: LegalKey) => setRead((r) => (r[k] ? r : { ...r, [k]: true })), []);
  const onLoaded = useCallback((k: LegalKey, d: LoadedDoc) => { if (k === 'ic' && d.version) setIcVersion(d.version); }, []);

  const agree = async () => {
    if (!done || busy) return;
    setBusy(true); setError('');
    try {
      await acceptTerms(icVersion, REQUIRED_DOCS.filter((k) => read[k]));
      onAccepted();
    } catch (e) {
      const status = (e as { status?: number })?.status;
      setError(status === 409
        ? 'The agreement was just updated. Reload the page to read the current version.'
        : "Couldn't save your agreement. Try again.");
      setBusy(false);
    }
  };

  const first = (name || '').trim().split(/\s+/)[0];

  return (
    <div className="agree-page" data-testid="agree-terms-page">
      <div className="agree-card">
        <h1>{first ? `Welcome, ${first}` : 'Before you start'}</h1>
        <p className="agree-lede">Read the contractor agreement and privacy policy once before your first job.</p>
        <div className="agree-rows">
          {REQUIRED_DOCS.map((k) => (
            <button key={k} type="button" className={`agree-row${read[k] ? ' is-read' : ''}`} onClick={() => setOpen(k)} aria-haspopup="dialog" data-testid={`legal-open-${k}`}>
              <span className="agree-row-title">{LEGAL_DOCS[k].title}</span>
              <span className="agree-row-state">{read[k] ? 'Read' : 'Open'}</span>
            </button>
          ))}
        </div>
        <button type="button" className="agree-btn" disabled={!done || busy} onClick={agree} data-testid="agree-terms-submit">
          {busy ? 'Saving…' : 'I agree'}
        </button>
        <p className={`agree-note${error ? ' is-error' : ''}`} role={error ? 'alert' : undefined}>
          {error || (done ? 'You agree to both documents as written.' : lockedReason(REQUIRED_DOCS, read) || 'Loading the agreement…')}
        </p>
        <button type="button" className="agree-out" onClick={onLogout}>Log out</button>
      </div>
      <LegalDocSheet doc={open} read={open ? !!read[open] : false} onRead={markRead} onLoaded={onLoaded} onClose={() => setOpen(null)} />
      <style>{`
        .agree-page{min-height:100dvh;display:flex;align-items:center;justify-content:center;padding:32px 20px;background:var(--c-bg,#141210);color:var(--c-ink,#F4F1EB);box-sizing:border-box;}
        .agree-card{width:100%;max-width:420px;min-width:0;}
        .agree-card h1{font-size:28px;font-weight:500;letter-spacing:-.8px;margin:0 0 10px;}
        .agree-lede{font-size:14px;line-height:1.55;color:var(--c-ink-soft);margin:0 0 24px;}
        .agree-rows{border:1px solid var(--c-hair-s);border-radius:2px;background:var(--c-bg-soft);margin:0 0 20px;}
        .agree-row{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;min-height:52px;padding:12px 16px;border:0;background:transparent;color:var(--c-ink);font:inherit;font-size:14px;text-align:left;cursor:pointer;box-sizing:border-box;}
        .agree-row + .agree-row{border-top:1px solid var(--c-hair);}
        .agree-row-title{min-width:0;text-decoration:underline;text-underline-offset:3px;text-decoration-color:var(--c-hair-s);}
        .agree-row.is-read .agree-row-title{text-decoration:none;}
        .agree-row-state{flex:none;font-size:13px;color:var(--c-ink-mute);}
        .agree-row.is-read .agree-row-state{color:var(--c-ink);}
        .agree-btn{width:100%;min-height:48px;border:0;border-radius:2px;background:var(--c-ink,#F4F1EB);color:var(--c-bg,#141210);font:inherit;font-size:15px;font-weight:500;cursor:pointer;}
        .agree-btn:disabled{opacity:.32;cursor:not-allowed;}
        .agree-note{font-size:13px;line-height:1.5;color:var(--c-ink-mute);margin:12px 0 28px;}
        .agree-note.is-error{color:var(--c-brick,#D2542B);}
        .agree-out{background:none;border:0;padding:0;font:inherit;font-size:13px;color:var(--c-ink-mute);text-decoration:underline;cursor:pointer;}
        .agree-row:focus-visible,.agree-btn:focus-visible,.agree-out:focus-visible{outline:2px solid var(--c-ink);outline-offset:2px;}
      `}</style>
    </div>
  );
}
