-- Migration: 20260601_agent_noshow_escalations
-- Tracks SMS escalation attempts before a no-show claim is filed.
-- Required by POST /api/vendor-os/jobs/{id}/escalate-absent-agent

CREATE TABLE IF NOT EXISTS agent_noshow_escalations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL,
    booking_id UUID,
    agent_phone TEXT,
    attempt_number INT NOT NULL DEFAULT 1,
    sms_sid TEXT,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivery_status TEXT NOT NULL DEFAULT 'pending'  -- 'sent', 'delivered', 'failed', 'no_phone'
);

-- Service role only: holds agent phone numbers. RLS on with no policies.
ALTER TABLE agent_noshow_escalations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE agent_noshow_escalations FROM anon, authenticated;

CREATE INDEX IF NOT EXISTS idx_agent_noshow_escalations_job_id
    ON agent_noshow_escalations(job_id);

CREATE INDEX IF NOT EXISTS idx_agent_noshow_escalations_sent_at
    ON agent_noshow_escalations(sent_at);

-- reconciliation_events: written by GET /api/admin/reconcile-noshow (dry_run=false)
CREATE TABLE IF NOT EXISTS reconciliation_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,         -- 'fee_without_retention' | 'retention_without_fee' | 'booking_missing'
    booking_id UUID,
    fee_id UUID,
    note TEXT,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Service role only. RLS on with no policies.
ALTER TABLE reconciliation_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE reconciliation_events FROM anon, authenticated;

CREATE INDEX IF NOT EXISTS idx_reconciliation_events_booking_id
    ON reconciliation_events(booking_id);
