# Runbook: switch a field between the paid and the free version

Decision D41; the matrix is in `docs/PLATFORM_MODES.md`.

1. Coolify → project *pack* → app stack → Environment Variables.
2. Change `KX_PLATFORM_PLAN` (every field) or one override, e.g. `KX_PLATFORM__STT=free`.
3. Make sure the vendor the new version needs is configured: free speech needs `KX_APP_SPEECH_URL` and the `speech` service healthy; paid speech needs `DEEPGRAM_API_KEY` / `ELEVEN_API_KEY`.
4. Redeploy `api`, `worker`, `beat` and `voice`.
5. Check: `kx platform show` in the api terminal; the `platform.resolved` log line in SigNoz; no `platform.paid_vendor_missing` warning.
6. Embeddings only (`KX_PLATFORM__EMBEDDINGS`): first change `EMBEDDING_MODEL_ID` in `compose.ai.yml` and redeploy `embeddings`, then switch the app setting, then run `kx reindex` (outside sales hours; BGE-M3 ≈ 30 min per 2,500 chunks). Search uses full-text for chunks not re-embedded yet.

Going to the free speech version also caps voice calls at once (`KX_APP_VOICE_MAX_CONCURRENT_FREE`); above it visitors get chat.
