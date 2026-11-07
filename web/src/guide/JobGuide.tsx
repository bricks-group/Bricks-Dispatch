import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import type { Job } from '../lib/api';
import { jobCaptureModes } from '../lib/capture-modes';
import { ButtonLink } from '../components/Button';
import { guide, guidedCaptureHref, hasGuidedCapture } from './guide-data';
import {
  PROPERTY_TYPES, clipPlan, jobPackage, jobPropertyType, jobServices, stepsForServices,
  type ClipRow, type PropertyType,
} from './guide-model';
import { StepBody } from './StepCard';
import './guide.css';

const typeKey = (jobId: string) => `bricks.guide.type.${jobId}`;
function savedType(jobId: string): PropertyType | null {
  try {
    const v = localStorage.getItem(typeKey(jobId));
    return PROPERTY_TYPES.some((t) => t.id === v) ? (v as PropertyType) : null;
  } catch { return null; }
}
const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function ClipGroup({ title, rows, note }: { title: string; rows: ClipRow[]; note?: string }) {
  if (!rows.length) return null;
  return (
    <div className="gd-clipgroup">
      <p className="gd-clip-h"><span>{title}</span><span className="gd-mono">{rows.length}</span></p>
      {note ? <p className="gd-note">{note}</p> : null}
      <ul className="gd-clips">
        {rows.map((r, i) => (
          <li key={`${r.id}-${i}`} className="gd-clip">
            <span className="gd-clip-id">{r.id}</span>
            <span className="gd-clip-main">
              <span className="gd-clip-name">{r.clip?.name ?? r.id}</span>
              <span className="gd-clip-meta">
                {r.clip ? sentence(r.clip.movement) : ''}{r.note ? ` · ${r.note}` : ''}
              </span>
            </span>
            {r.clip ? (
              <span className="gd-clip-s">
                {r.clip.capture_s[0] === r.clip.capture_s[1] ? r.clip.capture_s[0] : `${r.clip.capture_s[0]}–${r.clip.capture_s[1]}`} s
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Steps and clip list for one job, matched to its package and services. Phone first, for use on site. */
export function JobGuide({ job }: { job: Job }) {
  const pkg = jobPackage(job);
  const visit = jobCaptureModes(job).visit;
  const detected = jobPropertyType(job);
  const [type, setType] = useState<PropertyType>(() => savedType(job.id) || detected || 'house');
  const [tab, setTab] = useState<'steps' | 'clips'>('steps');
  useEffect(() => {
    setType(savedType(job.id) || detected || 'house');
    setTab('steps');
  }, [job.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const services = useMemo(() => (pkg ? jobServices(pkg, guide, { visit }) : null), [pkg, visit]);
  const steps = useMemo(() => stepsForServices(guide, services), [services]);
  const plan = useMemo(() => clipPlan(guide, pkg, type), [pkg, type]);
  const pkgName = pkg ? guide.packages.find((p) => p.id === pkg)?.name : null;
  const vvAddon = visit && pkg !== 'showcase' && pkg !== 'suite';

  const pickType = (t: PropertyType) => {
    setType(t);
    try { localStorage.setItem(typeKey(job.id), t); } catch { /* private mode */ }
  };

  const byPhase = guide.phases
    .map((ph) => ({ ph, items: steps.filter((s) => s.phase === ph.id) }))
    .filter((g) => g.items.length);

  return (
    <section className="card full gd-job" data-testid="job-guide">
      <h3>On-site guide</h3>
      <div className="gd-job-in">
        <p className="gd-job-meta">
          <span>{pkgName ? pkgName : 'Package not matched · every step'}{vvAddon ? ' · Virtual Visit' : ''}</span>
          <span className="gd-mono">{steps.length} steps{plan ? ` · ${plan.total} clips` : ''}</span>
        </p>

        {plan ? (
          <div className="gd-seg" role="tablist" aria-label="Guide view">
            <button type="button" role="tab" aria-selected={tab === 'steps'} className={tab === 'steps' ? 'on' : ''} onClick={() => setTab('steps')}>Steps</button>
            <button type="button" role="tab" aria-selected={tab === 'clips'} className={tab === 'clips' ? 'on' : ''} onClick={() => setTab('clips')}>Clip list</button>
          </div>
        ) : null}

        {tab === 'steps' || !plan ? (
          <div>
            {byPhase.map(({ ph, items }) => (
              <div key={ph.id} className="gd-job-phase">
                <p className="gd-eyebrow">{ph.title}</p>
                {items.map((s) => {
                  const vv = s.applies_to.includes('virtual_visit');
                  return (
                    <details key={s.id} className="gd-acc" data-testid="job-guide-step">
                      <summary>
                        <span className="gd-acc-t">{s.title}</span>
                        <svg className="gd-chev" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeWidth="1.25" /></svg>
                      </summary>
                      <div className="gd-acc-body">
                        <StepBody
                          step={s}
                          footer={vv ? (
                            <div className="gd-vv">
                              <p>Follow the on-screen steps in the app.</p>
                              {hasGuidedCapture ? <ButtonLink to={guidedCaptureHref(job.id)}>Open guided capture</ButtonLink> : null}
                            </div>
                          ) : null}
                        />
                      </div>
                    </details>
                  );
                })}
              </div>
            ))}
            <Link className="gd-more" to={pkg ? `/guide?package=${pkg}` : '/guide'}>Open the full guide</Link>
          </div>
        ) : (
          <div>
            <p className="gd-eyebrow">Property type</p>
            <div className="gd-chips" role="radiogroup" aria-label="Property type">
              {PROPERTY_TYPES.map((t) => (
                <button key={t.id} type="button" role="radio" aria-checked={type === t.id} className={type === t.id ? 'on' : ''} onClick={() => pickType(t.id)}>
                  {t.label}
                </button>
              ))}
            </div>
            {!detected ? <p className="gd-note">Property type is not on the job. Pick it to load the right list.</p> : null}
            <ClipGroup title="Horizontal" rows={plan.horizontal} />
            <ClipGroup title="Vertical" rows={plan.vertical} note="Native 9:16. Same ID, ending in _V." />
            <ClipGroup title="Drone" rows={plan.drone} note={plan.droneNote} />
            <ClipGroup title="Reference" rows={plan.reference} />
            <p className="gd-note">Hold 2 s, move, hold 2 s. Name files JobID_ClipID_T1.mov. Cannot capture a clip? Tell Bricks why before you leave.</p>
          </div>
        )}
      </div>
    </section>
  );
}
