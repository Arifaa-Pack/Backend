# Runbook: rotate secrets

All secrets are Coolify encrypted environment variables (no secret is in Git). Rotate yearly, when a person with access leaves, and at once after a suspected leak.

| Secret | How | Downtime |
| --- | --- | --- |
| Vendor API keys (Anthropic, Deepgram, ElevenLabs, Telnyx, Meta, AWS SES, R2) | create the new key at the vendor, update Coolify, redeploy the app stack, then revoke the old key | none (rolling) |
| Database role password | set `KX_PW_PACK_<DB>_APP`, run `db_bootstrap.sh` (re-syncs passwords), regenerate the PgBouncer `userlist.txt` line (`SELECT rolname, rolpassword FROM pg_authid WHERE rolname = 'pack_crm_app'`), update the DSNs, redeploy | seconds |
| `KX_JWT_PRIVATE_KEY` | new RSA key with a new `KX_JWT_KEY_ID`; staff sign in again | staff re-login |
| `KX_AUTH__ENCRYPTION_KEY` | **do not rotate casually**: every TOTP seed is encrypted with it; rotation means every staff member enrols again | staff re-enrol |
| `KX_SERVICE_TOKEN_SECRET` | update and redeploy; open website sessions and unsubscribe links in sent emails stop working | visitors restart chat |
| `KX_AUTO_TELNYX__CALLBACK_SECRET` | update and redeploy; calls in progress lose their callbacks | wait for no calls |
| `LIVEKIT_API_KEY/SECRET` | update both the `livekit` stack and the app stack | voice calls in progress drop |
| SSH keys, Coolify admin | per person; remove leavers at once | none |
