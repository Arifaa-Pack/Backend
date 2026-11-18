# Runbook: rotate secrets

All secrets are Coolify encrypted environment variables or root-only files in `/opt/kx/secrets/` on the host (`pack.env`, `pack-cms.env`, `userlist.txt`, `postgres_password`, `backup.env`, `walg-prune.env`, `pg-health.env`, `crowdsec-cloudflare-bouncer.yaml`); no secret is in Git or in the Coolify checkout. Rotate yearly, when a person with access leaves, and at once after a suspected leak.

| Secret | How | Downtime |
| --- | --- | --- |
| Vendor API keys (Anthropic, Deepgram, ElevenLabs, Telnyx, Meta, AWS SES, R2) | create the new key at the vendor, update Coolify, redeploy the app stack, then revoke the old key | none (rolling) |
| Database role password | set `KX_PW_PACK_<DB>_APP`, run `db_bootstrap.sh` **with `KX_BOOTSTRAP_ROTATE=1`** (without it existing passwords are left alone, M6: each `ALTER … PASSWORD` stores a new SCRAM salt, and PgBouncer only passes logins through with a byte-identical verifier), regenerate the PgBouncer `userlist.txt` line in `/opt/kx/secrets/userlist.txt` (`SELECT rolname, rolpassword FROM pg_authid WHERE rolname = 'pack_crm_app'`), update the DSNs, redeploy | seconds |
| `KX_JWT_PRIVATE_KEY` | new RSA key with a new `KX_JWT_KEY_ID`; staff sign in again | staff re-login |
| `KX_AUTH__ENCRYPTION_KEY` | **do not rotate casually**: every TOTP seed is encrypted with it; rotation means every staff member enrols again | staff re-enrol |
| `KX_SERVICE_TOKEN_SECRET` | update and redeploy; open website sessions and unsubscribe links in sent emails stop working | visitors restart chat |
| `KX_AUTO_TELNYX__CALLBACK_SECRET` | update and redeploy; calls in progress lose their callbacks | wait for no calls |
| `LIVEKIT_API_KEY/SECRET` | update both the `livekit` stack and the app stack | voice calls in progress drop |
| `REDIS_PASSWORD` | update it in Coolify and in the two redis URLs of `pack.env` together, redeploy the app stack | seconds (queued jobs stay in the AOF) |
| `PHOENIX_SECRET`, Phoenix API key | new secret: everyone signs in again; new system key in Phoenix → `KX_APP_AI_TRACE_HEADERS`, then delete the old key | none |
| `WALG_LIBSODIUM_KEY`, `KX_BACKUP_PASSPHRASE` | **only with a fresh full backup**: old backups stay readable with the old key only, so keep the old key offline until they age out (7 / 14 days) | none |
| SSH keys, Coolify admin | per person; remove leavers at once | none |
