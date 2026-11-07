import { Link } from 'react-router-dom';
import { CALENDAR_NUDGE_BODY, CALENDAR_NUDGE_TITLE } from '../lib/calendarCopy';

/** Visible empty-state on Today when no calendar is connected. Not a toast. */
export function CalendarConnectNudge({ linkToAvailability = true }: { linkToAvailability?: boolean }) {
  return (
    <section className="card full" data-testid="calendar-connect-nudge">
      <h3>{CALENDAR_NUDGE_TITLE}</h3>
      <p className="muted" style={{ margin: '8px 0 0', padding: 0, maxWidth: 680 }}>
        {CALENDAR_NUDGE_BODY}
      </p>
      {linkToAvailability ? (
        <p style={{ margin: '12px 0 0', padding: 0 }}>
          <Link to="/availability">Open Availability</Link>
        </p>
      ) : null}
    </section>
  );
}
