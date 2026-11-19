# ADR-0003: The CMS is a platform package (`kx.cms`), not Payload

- **Status:** accepted by Kairox (19 Nov 2026); the change to SRS §4 and the server plan v2 awaits Arifa's and
  Arifa IT's approval (OPEN_QUESTIONS #79)
- **Date:** 2026-11-19
- **Deciders:** Kairox engineering lead (Hamthan)
- **References:** SRS §3, §4, §6, §7; D54, D55, D61; WP-C1, WP-C2; design doc v4 (Rizwan, 18 Nov) §7;
  `docs/handoff/CMS_API.md`

## Context

The SRS (§4) names Payload as the CMS, "on our dedicated server", in its own image built from a separate
repository. That repository was never started. The platform only had the two ends of the Payload contract: a
publish webhook (`kx/automation/cms`) and a REST reader / draft writer in the app's adapters. Meanwhile:

* the website (Next.js) reads static files (`catalogue.json`, `site.ts`, `clients.json`), so the client cannot
  edit anything, and **the frontend developer has no content endpoints to build against**;
* the AI must move the website to what a visitor asks for (SRS §6 step 2). For that it needs a site map that
  always agrees with what the website shows (D55);
* the product workbook (18 Nov) must save website details as drafts, and published products must reach the CRM
  by code, without prices.

A separate Node service would add a fourth runtime, a second admin login, a second backup and a second set of
roles, and the platform would still have to keep a webhook, a reader and a writer for it in sync.

## Decision

1. The CMS is a **platform package**, `packages/kx-cms` → `kx.cms`, beside `kx.crm`, `kx.automation` and
   `kx.agents`. It imports only `kx.core` and owns its **own logical database `cms`** (schema `cms`; the database
   and the `cms_app` role already existed for Payload). Its migrations start at `cms_0001`.
2. **Content**: typed collections (`site`, `pages` with blocks keyed by the website's DOM section ids,
   `families`, `products`, `tour_chapters`, `stations`, `faqs`, `certificates`, `clients`, `industries`, `stories`,
   `leadership`). Each document is stored per language, English complete and Arabic partial.
3. **Workflow**: draft → preview (signed token, Next.js draft mode) → publish. Every published version is kept
   and can be restored, and an optimistic lock (`revision`) stops one editor overwriting another. **No prices**:
   price-like fields and money in text are refused (`kx.core.no_prices`).
4. **Publishing** writes its audit row and its events in one transaction, through the CMS outbox (relayed by
   Beat like the other databases):
   * `content.published.v1` → the website re-renders (`POST /api/revalidate`, HMAC);
   * `kb.content_published.v1` → the AI's knowledge base;
   * `catalogue.product_published.v1` → the CRM product record.
5. **APIs**: public reads `/v1/content/*` (cached, ETag), editors `/v1/cms/*` (staff JWT; new role
   `content_editor`, CMS only, SRS §3), media uploads straight to R2 with presigned URLs (content-addressed keys).
6. **Site manifest (D55)**: built from published content and served to the AI (`SiteManifestSource` in
   `kx.core.contracts`) and to the website (`/v1/content/manifest`).
7. **Products (D61)**: a CMS product is the website's marketing record (key = the site id, e.g. `ac-8325`). The
   sellable variants (ERP items) are CRM products linked to it; prices and stock stay in the CRM.
8. Payload's webhook stays until the SRS change is approved, then it and the `cms` service in
   `infra/server/stacks/compose.app.yml` are removed.

## Consequences

* One login, one backup, one set of roles, one deployment. The CMS is reviewed and tested like the rest
  (import-linter layers, mypy strict, DB tests), and `make handoff` generates its API document and Postman
  collection.
* The admin screens are built in the CRM web app against `/v1/cms`. `GET /v1/cms/collections` returns each
  type's JSON Schema, so a generic form covers new fields without frontend work.
* The website must accept the revalidate call and use the content API instead of its static files
  (FRONTEND_GUIDE.md).
* We now own editor features Payload would have given us (rich text, image cropping). Version 1 keeps text
  plain, with blocks; rich text can be added as a block `kind` later.
* If the client later wants a third-party CMS, the `kx.cms` routers are the contract to keep; ADR-0002 (package
  split) applies.

## Alternatives rejected

* **Payload, as in the SRS**: a separate repository, image and runtime nobody had started; a second admin and
  role model; three integration points to keep in sync. Its draft and version features are what we built here.
* **The CMS inside the CRM package**: rejected by the user (19 Nov). The CRM's database and roles are about
  deals; content editors have no CRM access (SRS §3); keeping it separate follows the package walls.
* **Content in the website's repository (static files)**: no editing without a developer (SRS §7 forbids it),
  and the AI would read a different source than the site.
