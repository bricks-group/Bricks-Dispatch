import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { LEGAL_DOCS, isScrolledToEnd, loadLegalDoc, type LegalKey, type LoadedDoc } from '../../lib/legal-docs';

const FOCUSABLE = 'a[href],button:not([disabled]),[tabindex]:not([tabindex="-1"])';

type State = { status: 'loading' | 'ready' | 'error'; doc?: LegalKey; data?: LoadedDoc };

/**
 * A legal document as a pop-up: centred dialog on desktop, full-height bottom
 * sheet on phones. Reaching the end (within 24px) marks it read. Esc closes,
 * focus is trapped, page scroll is locked.
 */
export function LegalDocSheet({ doc, read, onRead, onLoaded, onClose }: {
  doc: LegalKey | null;
  read: boolean;
  onRead: (k: LegalKey) => void;
  onLoaded?: (k: LegalKey, d: LoadedDoc) => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<State>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const meta = doc ? LEGAL_DOCS[doc] : null;

  useEffect(() => {
    if (!doc) return undefined;
    let live = true;
    setState({ status: 'loading', doc });
    loadLegalDoc(doc).then(
      (data) => { if (live) { setState({ status: 'ready', data, doc }); onLoaded?.(doc, data); } },
      () => { if (live) setState({ status: 'error', doc }); },
    );
    return () => { live = false; };
  }, [doc, attempt, onLoaded]);

  const check = useCallback(() => {
    const box = bodyRef.current;
    if (!box || !doc || state.status !== 'ready' || state.doc !== doc) return;
    if (isScrolledToEnd(box) && !read) onRead(doc);
  }, [doc, read, onRead, state.status, state.doc]);

  useEffect(() => {
    if (state.status !== 'ready') return undefined;
    const box = bodyRef.current;
    if (!box) return undefined;
    const raf = requestAnimationFrame(check);
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(check) : null;
    ro?.observe(box);
    if (box.firstElementChild) ro?.observe(box.firstElementChild);
    return () => { cancelAnimationFrame(raf); ro?.disconnect(); };
  }, [state.status, check]);

  useEffect(() => {
    if (!doc) return undefined;
    const prevFocus = document.activeElement as HTMLElement | null;
    const y = window.scrollY;
    const b = document.body.style;
    const prev = { position: b.position, top: b.top, left: b.left, right: b.right, width: b.width, overflow: b.overflow };
    Object.assign(b, { position: 'fixed', top: `-${y}px`, left: '0', right: '0', width: '100%', overflow: 'hidden' });
    const t = window.setTimeout(() => bodyRef.current?.focus({ preventScroll: true }), 0);
    return () => {
      window.clearTimeout(t);
      Object.assign(b, prev);
      window.scrollTo(0, y);
      prevFocus?.focus?.({ preventScroll: true });
    };
  }, [doc]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') { e.stopPropagation(); onClose(); return; }
    if (e.key !== 'Tab' || !panelRef.current) return;
    const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  if (!doc || !meta) return null;
  const view: State = state.doc === doc ? state : { status: 'loading' };

  return createPortal(
    <div className="ldoc-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={panelRef} className="ldoc-panel" role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={onKeyDown} data-testid={`legal-sheet-${doc}`}>
        <div className="ldoc-grip" aria-hidden="true" />
        <header className="ldoc-head">
          <div style={{ minWidth: 0 }}>
            <h2 id={titleId}>{meta.title}</h2>
            {view.data?.updated ? <p className="ldoc-updated">{view.data.updated}</p> : null}
          </div>
          <button type="button" className="ldoc-x" onClick={onClose} aria-label="Close">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="square" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </header>
        <div ref={bodyRef} className="ldoc-body" tabIndex={0} onScroll={check} aria-label={`${meta.title} text`} data-testid="legal-sheet-body">
          {view.status === 'ready' && view.data ? (
            <div className="ldoc-content" dangerouslySetInnerHTML={{ __html: view.data.html }} />
          ) : view.status === 'error' ? (
            <div className="ldoc-msg" role="alert">
              <p>This document didn&apos;t load.</p>
              <p><button type="button" className="ldoc-link" onClick={() => setAttempt((n) => n + 1)}>Try again</button></p>
            </div>
          ) : (
            <div className="ldoc-msg" aria-live="polite"><p>Loading…</p></div>
          )}
        </div>
        <footer className="ldoc-foot">
          <p className={`ldoc-cue${read ? ' is-done' : ''}`} aria-live="polite" data-testid="legal-sheet-cue">
            {read ? 'Read' : 'Scroll to the end to continue'}
          </p>
          <button type="button" className="ldoc-done" disabled={!read} onClick={onClose} data-testid="legal-sheet-done">
            I&apos;ve read this
          </button>
        </footer>
      </div>
      <style>{LEGAL_SHEET_CSS}</style>
    </div>,
    document.body,
  );
}

const LEGAL_SHEET_CSS = `
.ldoc-overlay{position:fixed;inset:0;z-index:1000;background:rgba(10,9,8,.6);display:flex;align-items:center;justify-content:center;padding:24px;overscroll-behavior:contain;animation:ldocFade .18s ease-out;}
.ldoc-panel{display:flex;flex-direction:column;width:100%;max-width:720px;height:min(86vh,880px);background:var(--c-bg,#141210);color:var(--c-ink,#F4F1EB);border:1px solid var(--c-hair-s);border-radius:4px;overflow:hidden;animation:ldocRise .22s cubic-bezier(.2,.8,.2,1);}
.ldoc-grip{display:none;}
.ldoc-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:20px 24px 16px;border-bottom:1px solid var(--c-hair);}
.ldoc-head h2{margin:0;font-size:19px;font-weight:500;letter-spacing:-.3px;line-height:1.25;}
.ldoc-updated{margin:6px 0 0;font-size:12px;color:var(--c-ink-mute);}
.ldoc-x{flex:none;display:inline-flex;align-items:center;justify-content:center;width:40px;height:40px;margin:-8px -10px 0 0;border:0;border-radius:2px;background:transparent;color:var(--c-ink-soft);cursor:pointer;}
.ldoc-body{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;overscroll-behavior:contain;padding:8px 24px 28px;outline:none;}
.ldoc-content{font-size:14px;line-height:1.62;color:var(--c-ink-soft);overflow-wrap:anywhere;}
.ldoc-content section + section{border-top:1px solid var(--c-hair);margin-top:20px;padding-top:4px;}
.ldoc-content h2{font-size:16px;font-weight:500;color:var(--c-ink);margin:20px 0 10px;}
.ldoc-content h3{font-size:14.5px;font-weight:500;color:var(--c-ink);margin:18px 0 6px;}
.ldoc-content p{margin:0 0 10px;}
.ldoc-content ul,.ldoc-content ol{margin:0 0 10px 18px;padding:0;}
.ldoc-content a{color:var(--c-ink);text-decoration:underline;text-underline-offset:2px;}
.ldoc-content table{width:100%;border-collapse:collapse;margin:0 0 14px;font-size:13px;display:block;overflow-x:auto;}
.ldoc-content th,.ldoc-content td{text-align:left;padding:8px 10px;border:1px solid var(--c-hair);vertical-align:top;}
.ldoc-text{white-space:pre-wrap;font-size:13.5px;padding-top:12px;}
.ldoc-msg{padding:32px 0;color:var(--c-ink-mute);font-size:14px;}
.ldoc-link{background:none;border:0;padding:0;font:inherit;color:var(--c-ink);text-decoration:underline;cursor:pointer;}
.ldoc-foot{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 24px;border-top:1px solid var(--c-hair);}
.ldoc-cue{margin:0;font-size:13px;color:var(--c-ink-mute);}
.ldoc-cue.is-done{color:var(--c-ink);}
.ldoc-done{flex:none;min-height:44px;padding:0 20px;border:0;border-radius:2px;background:var(--c-ink,#F4F1EB);color:var(--c-bg,#141210);font:500 14px/1 inherit;font-family:inherit;cursor:pointer;}
.ldoc-done:disabled{opacity:.32;cursor:not-allowed;}
.ldoc-done:focus-visible,.ldoc-x:focus-visible,.ldoc-link:focus-visible{outline:2px solid var(--c-ink);outline-offset:2px;}
@keyframes ldocFade{from{opacity:0}to{opacity:1}}
@keyframes ldocRise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes ldocSheet{from{transform:translateY(100%)}to{transform:none}}
@media (max-width:640px){
  .ldoc-overlay{align-items:flex-end;padding:0;padding-top:max(12px,env(safe-area-inset-top));}
  .ldoc-panel{max-width:none;height:100%;border-radius:12px 12px 0 0;border-bottom:0;animation:ldocSheet .26s cubic-bezier(.2,.8,.2,1);}
  .ldoc-grip{display:block;width:36px;height:4px;border-radius:2px;background:var(--c-hair-s);margin:8px auto 0;}
  .ldoc-head{padding:12px 20px 14px;}
  .ldoc-body{padding:4px 20px 24px;}
  .ldoc-foot{padding:12px 20px calc(12px + env(safe-area-inset-bottom));}
}
@media (prefers-reduced-motion:reduce){.ldoc-overlay,.ldoc-panel{animation:none;}}
`;
