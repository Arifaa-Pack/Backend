# Manual-testing pack (QA tester)

Everything a QA tester needs to test the platform by hand with Postman, against the staging server or the
developer's local live-test stack. Start with `Kairox_Manual_Test_Guide.docx`.

| File | What it is |
|---|---|
| `Kairox_Manual_Test_Guide.docx` | The tester's guide: product in one page, Postman setup, roles, first sign-in with the tester's own authenticator app, 21 end-to-end scenarios, negative and security tests, Postman limits, bug template, endpoint reference, variable list. |
| `postman/Kairox.postman_collection.json` | Postman v2.1 collection: one request per API route (folders 00-13, in test order) + folder 14 with 20 requests that must be refused. Scripts save ids and tokens into the environment and sign the WhatsApp, CMS and Telnyx test webhooks. |
| `postman/Kairox-local.postman_environment.json` | Settings for the local live-test stack (`http://localhost:38000`). Holds the live-test stack's TEST-only WhatsApp secret; nothing else secret. |
| `postman/Kairox-staging.postman_environment.json` | Settings for staging: placeholder address, every secret blank ("ask the admin"). |
| `endpoints.json` | Every route (method, path, who may call, MFA, purpose, expected status), read by `build_guide.js`. |
| `build_guide.js` | Builds the Word guide from `endpoints.json` + the local environment file. |

## Regenerate (after any API change)

```bash
# 1. Postman files + endpoints.json, offline (no DB, no network); same venv as the app
uv run python scripts/gen_tester_pack.py
# 2. the Word guide (needs the `docx` npm package; install it outside the repo, e.g. once:
#    npm install --prefix "$TEMP/kxdocx" docx@9   and   export NODE_PATH="$TEMP/kxdocx/node_modules")
node docs/testing/build_guide.js
```

`gen_tester_pack.py` prints the OpenAPI operation count and the collection route count and stops with an error
if they differ. Never put passwords, authenticator keys or real customer data into these files; staging and
production secrets go only into a tester's own Postman "Current value".
