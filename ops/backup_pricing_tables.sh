#!/bin/bash
# Back up packages + vendor_payout_rates on api-2 before migration 0015. Prints no secrets.
set -euo pipefail
S="ssh -i $HOME/.ssh/bricks_oracle -o UserKnownHostsFile=$HOME/.ssh/known_hosts_bricks_oracle -o BatchMode=yes ubuntu@150.230.24.195"
TS=$(date +%Y%m%d-%H%M)
$S "sudo -u postgres pg_dump -d bricks --data-only -t public.packages -t public.vendor_payout_rates" > /workspace/pricing-audit-1121/backup-pricing-$TS.sql
wc -l /workspace/pricing-audit-1121/backup-pricing-$TS.sql
