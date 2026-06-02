-- ops_escalations: records dispatch failures where no eligible vendor could be assigned.
-- Written by _escalate_no_vendor in vendor_selection.py.
CREATE TABLE IF NOT EXISTS ops_escalations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id      text,
    job_id          text,
    escalation_type text NOT NULL,
    attempt_count   integer NOT NULL DEFAULT 1,
    error_message   text,
    status          text NOT NULL DEFAULT 'open',
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ops_escalations_job_id_idx     ON ops_escalations (job_id);
CREATE INDEX IF NOT EXISTS ops_escalations_booking_id_idx ON ops_escalations (booking_id);
CREATE INDEX IF NOT EXISTS ops_escalations_status_idx     ON ops_escalations (status);
