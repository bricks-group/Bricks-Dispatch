# Ops daily fix — ready, NOT deployed (updated Nov 15, 2026 after Sentinel re-check r2: C3 fixed)
Branch ops-r2-backup-1115 on top of live Dispatch 2418c840 (api-2 150.230.24.195). Files:
backend/app/services/ops_autopilot.py, backend/app/services/r2_storage.py, backend/tests/test_backup_targets.py.

- backup(): R2 bucket **bricks-backups, hard-coded** (no env override; any write/delete outside
  bricks-backups/db/YYYY-MM-DD.jsonl.gz or list outside prefix db/ raises). PUT with Content-MD5,
  HEAD read-back of size + ETag(MD5), prune >14 days by strict fullmatch name only.
- Fallback: /var/lib/bricks/db-backups (owner bricks, 0700), files dispatch-YYYY-MM-DD.jsonl.gz (0600),
  own prune; refuses the root stopgap dir. Daily email shows a warning when it falls back.
- Endpoint pinned (C3): always https://<account id>.r2.cloudflarestorage.com, account id must be 32 lowercase hex;
  BACKUP_R2_ENDPOINT and R2_ENDPOINT are ignored, and any other endpoint is refused at request time.
  Empty key refused for PUT/DELETE/HEAD. Existing fallback dir: must be owned by the running user, chmod 0700.
- Creds: BACKUP_R2_ACCOUNT_ID / BACKUP_R2_ACCESS_KEY_ID / BACKUP_R2_SECRET_ACCESS_KEY (else R2_*).
  Kam chose to reuse the key derived from the box Cloudflare token: put ONLY the derived pair
  (key id 03865f…, secret = sha256(token)) on api-2, never the raw token. Never printed.
- Direct-Postgres fallback when PostgREST 402s: sweep-run read, heartbeat insert, once/seen/unclaim/
  events_like, probation + escalation digests. Null job_id guarded. L1 redaction: pwa_web_push_devices
  endpoint/p256dh/auth, calendar_oauth_nonces.nonce_sha256.

Deploy (only after Sentinel re-check GO):
1. ts=$(date +%Y%m%d%H%M); sudo tar -czf /opt/bricks/backups/dispatch-api-opsr2-$ts.tar.gz -C /opt/bricks dispatch-api/app/services/ops_autopilot.py dispatch-api/app/services/r2_storage.py dispatch-api/DEPLOY_SHA
2. sudo install -d -o bricks -g bricks -m 0700 /var/lib/bricks/db-backups   (create /var/lib/bricks first if missing: sudo install -d -o bricks -g bricks -m 0750 /var/lib/bricks)
3. Copy the 2 service files into /opt/bricks/dispatch-api/app/services/ (owner as existing files); write new SHA to DEPLOY_SHA.
4. Env guard first: `sudo grep -cE '^(BACKUP_R2_ENDPOINT|R2_ENDPOINT|BACKUP_R2_BUCKET|R2_BUCKET_BACKUPS)=' /etc/bricks/oracle-backend.env` must print 0 (also check `systemctl show bricks-dispatch-api -p Environment`); STOP if not. Then back up /etc/bricks/oracle-backend.env, append BACKUP_R2_ACCOUNT_ID, BACKUP_R2_ACCESS_KEY_ID, BACKUP_R2_SECRET_ACCESS_KEY (root 0600). Do NOT add BACKUP_R2_ENDPOINT / R2_ENDPOINT / BACKUP_R2_BUCKET (ignored by code, but keep env clean).
5. sudo systemctl restart bricks-dispatch-api; check /health 200.
6. Verify without email: curl /api/ops/daily?notify=false with CRON_SECRET (note: this DOES run a real backup). Expect problems=[] and backup.object r2://bricks-backups/db/<date>.jsonl.gz with md5.
7. Confirm `sudo -u bricks test -w /var/lib/bricks/db-backups` and `stat -c '%U %a' /var/lib/bricks/db-backups` = `bricks 700`.
   Step 6 also proves R2 accepts Content-MD5 on the presigned PUT (box test already did: same object, md5 verified).
**Keep bricks-local-db-backup.timer ON** (second copy). Revisit after the fallback and R2 are proven.
Rollback: restore the tar, restart. No schema change.
Open for Kam (Sentinel M3): confirm in Cloudflare dashboard bricks-backups has r2.dev Disabled and no custom domain; set a date to swap to a bricks-backups-only key; remove bricks-social-approved from the box token.
