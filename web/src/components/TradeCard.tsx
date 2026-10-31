import { COI_LINE, tradeOf } from '../lib/trades';
import type { VendorProfile } from '../lib/api';

/** Profile card: the member's trade, its limits, how a Bricks job runs, and whether a COI is needed. */
export function TradeCard({ profile }: { profile: VendorProfile }) {
  const t = tradeOf(profile);
  return (
    <section className="card full" data-testid="trade-card">
      <h3>Your trade</h3>
      <div className="stack">
        <p style={{ margin: 0 }}>{t.label}</p>
        <p className="muted" style={{ padding: 0 }}>{t.work}</p>
        {t.limits.map((l) => <p key={l} className="muted" style={{ padding: 0 }}>{l}</p>)}
        <ol style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
          {t.steps.map((s) => <li key={s} className="muted">{s}</li>)}
        </ol>
        <p className={t.needsCoi ? '' : 'muted'} style={{ padding: 0, margin: 0 }} data-testid="trade-coi">
          {t.needsCoi ? COI_LINE : 'No certificate of insurance needed for this trade.'}
        </p>
      </div>
    </section>
  );
}
