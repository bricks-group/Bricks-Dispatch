import type { ReactNode } from 'react';
import type { GuideStep } from './guide-model';
import { diagramMarkup } from './guide-data';

function Tick() {
  return (
    <svg className="gd-mark" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M2 6.5 4.8 9 10 3" fill="none" stroke="currentColor" strokeWidth="1.25" />
    </svg>
  );
}
function Cross() {
  return (
    <svg className="gd-mark" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M3 3l6 6M9 3 3 9" fill="none" stroke="currentColor" strokeWidth="1.25" />
    </svg>
  );
}

/** Diagram, body and do / don't lists for one step. Shared by the Guide tab and the job guide. */
export function StepBody({ step, footer }: { step: GuideStep; footer?: ReactNode }) {
  const svg = diagramMarkup(step.diagram);
  return (
    <>
      {svg ? (
        <figure className="gd-diagram" role="img" aria-label={step.diagram_alt || step.title}>
          <div dangerouslySetInnerHTML={{ __html: svg }} />
        </figure>
      ) : null}
      <p className="gd-body">{step.body}</p>
      <div className="gd-dodont">
        {step.do.length ? (
          <div>
            <p className="gd-dd-h">Do</p>
            <ul className="gd-list gd-do">
              {step.do.map((d) => <li key={d}><Tick /><span>{d}</span></li>)}
            </ul>
          </div>
        ) : null}
        {step.dont.length ? (
          <div>
            <p className="gd-dd-h">Don’t</p>
            <ul className="gd-list gd-dont">
              {step.dont.map((d) => <li key={d}><Cross /><span>{d}</span></li>)}
            </ul>
          </div>
        ) : null}
      </div>
      {footer}
    </>
  );
}

export function StepCard({ step, n, highlight, footer }: { step: GuideStep; n: number; highlight?: boolean; footer?: ReactNode }) {
  return (
    <article id={`step-${step.id}`} className={`gd-card${highlight ? ' gd-card-hi' : ''}`} data-testid="guide-step">
      <header className="gd-card-head">
        <span className="gd-num">{String(n).padStart(2, '0')}</span>
        <h3 className="gd-title">{step.title}</h3>
      </header>
      <StepBody step={step} footer={footer} />
    </article>
  );
}
