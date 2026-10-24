import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { guide } from './guide-data';
import { jobServices, stepsForServices, type PackageId } from './guide-model';
import { StepCard } from './StepCard';
import './guide.css';

/** Guide tab: every phase and step of the Bricks Standard vendor guide, one card per step. */
export function GuidePage() {
  const [params, setParams] = useSearchParams();
  const focus = params.get('step') || '';
  const fromUrl = params.get('package') || '';
  const [pkg, setPkg] = useState<PackageId | ''>(guide.packages.some((p) => p.id === fromUrl) ? (fromUrl as PackageId) : '');

  const steps = useMemo(() => stepsForServices(guide, pkg ? jobServices(pkg, guide) : null), [pkg]);
  const numbered = useMemo(() => new Map(guide.steps.map((s, i) => [s.id, i + 1])), []);
  const phases = guide.phases.map((ph) => ({ ph, items: steps.filter((s) => s.phase === ph.id) })).filter((g) => g.items.length);

  useEffect(() => {
    if (focus) document.getElementById(`step-${focus}`)?.scrollIntoView({ block: 'start' });
  }, [focus]);

  const choose = (id: PackageId | '') => {
    setPkg(id);
    const next = new URLSearchParams(params);
    if (id) next.set('package', id); else next.delete('package');
    next.delete('step');
    setParams(next, { replace: true });
  };
  const jump = (id: string) => document.getElementById(`phase-${id}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' });

  return (
    <div className="page-content gd-page" data-testid="guide-page">
      <div className="page-header">
        <div>
          <h1>Guide</h1>
          <p>The Bricks Standard, step by step. You capture, Bricks edits. <span className="gd-mono">v{guide.version}</span></p>
        </div>
      </div>

      <div className="gd-filter">
        <p className="gd-eyebrow">Package</p>
        <div className="gd-chips" role="radiogroup" aria-label="Package">
          <button type="button" role="radio" aria-checked={!pkg} className={!pkg ? 'on' : ''} onClick={() => choose('')}>All</button>
          {guide.packages.map((p) => (
            <button key={p.id} type="button" role="radio" aria-checked={pkg === p.id} className={pkg === p.id ? 'on' : ''} onClick={() => choose(p.id)}>{p.name}</button>
          ))}
        </div>
      </div>

      <div className="gd-phonebar">
        <label className="gd-field">
          <span>Package</span>
          <select value={pkg} onChange={(e) => choose(e.target.value as PackageId | '')} aria-label="Package">
            <option value="">All packages</option>
            {guide.packages.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label className="gd-field">
          <span>Jump to</span>
          <select value="" onChange={(e) => { if (e.target.value) jump(e.target.value); e.target.value = ''; }} aria-label="Jump to section">
            <option value="">Section</option>
            {phases.map(({ ph, items }) => <option key={ph.id} value={ph.id}>{ph.title} ({items.length})</option>)}
          </select>
        </label>
      </div>

      <div className="gd-layout">
        <nav className="gd-toc" aria-label="Phases">
          {phases.map(({ ph, items }) => (
            <a key={ph.id} href={`#phase-${ph.id}`} onClick={(e) => { e.preventDefault(); jump(ph.id); }}>
              <span>{ph.title}</span><span className="gd-mono">{items.length}</span>
            </a>
          ))}
        </nav>
        <div className="gd-phases">
          {phases.map(({ ph, items }) => (
            <section key={ph.id} id={`phase-${ph.id}`} className="gd-phase">
              <h2 className="gd-phase-title"><span className="gd-mono">{String(ph.order).padStart(2, '0')}</span>{ph.title}</h2>
              {ph.id === 'virtual-visit' ? (
                <p className="gd-phase-note">Follow the on-screen steps in the app. The diagrams show the height, path and pace it asks for.</p>
              ) : null}
              <div className="gd-grid">
                {items.map((s) => (
                  <StepCard key={s.id} step={s} n={numbered.get(s.id) || 0} highlight={s.id === focus} />
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
