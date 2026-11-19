# CMS API: frontend hand-off

For the **website developer** (Arifa Pack site, Next.js, github.com/rizwankairoxaitech/Arifa-Pack) and the
developer of the **CMS admin screens** (in the CRM web app). The CMS is the platform package `kx.cms` (D54): the
client team edits the website's text, photos, videos, products, colours and SEO there, without a developer, and
the website shows the change within about a minute. This document is generated from the code (`make handoff`);
the endpoint reference at the end is always the server's real contract.

* Website reads: **`/v1/content/*`**, public, no login, cached.
* Editors: **`/v1/cms/*`**, staff login (the CRM's `/v1/auth`, see CRM_API.md §3), roles content editor, admin,
  manager, MD.
* Mock server for building before the backend runs:

```bash
npx @stoplight/prism-cli mock docs/handoff/openapi/openapi-cms.json --port 4012
```

---

## 1. How a change reaches the website (and the AI, and the CRM)

```
editor ──PUT draft──► /v1/cms ──publish──► kx.cms ──(same transaction)──► outbox: content.published.v1
                                                                         kb.content_published.v1 (per language)
                                                                         catalogue.product_published.v1 (products)
outbox ──relay (every few s)──► job "app.cms_revalidate" ──POST {website}/api/revalidate (HMAC)──► Next.js
                                                                                     revalidateTag / revalidatePath
website ──GET /v1/content/... (tags)──► fresh content          AI: site map + knowledge re-indexed; CRM: product details
```

1. The editor saves a **draft** (`PUT /v1/cms/collections/{collection}/{key}`), previews it on the website
   (§5), and **publishes** it (`POST .../publish`).
2. In the same transaction the CMS records the events above. Every published version is kept and can be
   restored.
3. Within seconds the platform calls the website: **`POST {KX_APP_WEBSITE_URL}/api/revalidate`** (§4). The site
   re-renders the affected pages. **The change is live in about a minute** (SRS §7).
4. Fallback: give every content fetch `next: { revalidate: 60, tags: [...] }`, so a missed call still heals
   within a minute.
5. The same publish rebuilds the AI's **site map** (`GET /v1/content/manifest`, D55), re-indexes the AI's
   **knowledge**, and updates the **CRM** product record. The website, the assistant and the sales team
   therefore always see the same content.

**Prices never appear in content** (SRS §7, D22): a field named like a price, or a text stating an amount of
money (`AED 1.20`, `3.5 dirhams`, `$2`), is refused on save with `422 rule_violation` (`reason: cms_price`).
Percentages ("80 % recycled aluminium") are fine. Prices live only in the CRM and reach a buyer only inside a
manager-approved quotation.

---

## 2. Languages

Every document is stored per language: `{"en": {...}, "ar": {...}}`. **English is complete**; Arabic holds
only what is translated. The API merges them for you: `?locale=ar` returns the Arabic values laid over the
English ones, field by field. Lists of objects (page blocks, FAQs) merge item by item, so block 3's Arabic
heading can be translated alone (design doc §6: "Text without an Arabic entry stays in English"). Codes, numbers
and media are shared. Pages stay English and Arabic; the assistant speaks six languages but always links to
`/…` or `/ar/…`.

---

## 3. The content model: what each page section reads

Collections (`GET /v1/cms/collections` returns each one's JSON Schema, so the admin screens can draw a form
for each type without a code change):

| Collection | Key | What it holds |
|---|---|---|
| `site` | `site` (one document) | Company name, phones, emails, addresses, hours, social links, announcement bar, **theme colours and fonts**, the assistant's fixed texts (greeting, recording notice, hand-over, busy, offline), `product_pages` (PG-03 live?) |
| `pages` | `home`, `products`, `about`, `quality`, `clients`, `contact`, `privacy`, … | Title, path, SEO / AEO / GEO, FAQs, and an ordered list of **blocks**: one per section, keyed by `section_id` |
| `families` | `containers`, `foil`, `platters`, `cups-trays`, `pots`, `microwave`, `cutlery`, `cling-film` | Name, short name, blurb, clip, hero media, accent colour, **synonyms per language** (what visitors call it; the AI uses them), SEO |
| `products` | the site's id (`ac-8325`, `sr-con-8342`, `foil-45-blue` …) | `family`, `ref` (8325), `code` (AC-1), `name`, `qty` (packing), `range` (trade / self), descriptions, specs, oven / microwave / freezer, applications, photos, video, datasheet, 3D model, FAQs, `legacy_paths` (old-site addresses), SEO |
| `tour_chapters` | `arrival`, `forming`, `lids`, `foil`, `packing`, `moulding`, `warehouse` | HM-05 chapter title, the film time in `seconds`, linked family |
| `stations` | `material-in` … `dispatch` | QA-02 the seven checked stations |
| `faqs`, `certificates` | free keys | Questions and answers; certificate title, issuer, file, validity |
| `clients`, `industries`, `stories` | free keys | CL-02 logos, CL-01 the six delivery stops (trade, use, product kit), CL-03 stories. All have `sample: true` until Arifa approves |
| `leadership` | free keys | AB-05 / AB-06 the CEO and the directors (role, quote, message, photo) |

**`section_id` is a contract with the website and the AI (D55):** it is the DOM `id` the page renders and the id
the assistant scrolls to (`scroll_to {section}`). Never rename one without changing the site.

### Page sections → CMS fields (design doc v4 IDs)

| Design ID | Page / section | `section_id` (DOM id) | Where the content lives |
|---|---|---|---|
| SH-02 / SH-03 / SH-04 | Header, menu, footer | (shared) | `site`: phones, emails, addresses, hours; `families` for the footer list |
| SH-05 | Assistant texts | (panel) | `site.assistant` |
| HM-01 | Home hero | `top` | `pages/home` block `top` (heading = the line), `families` (the eight, in order) |
| HM-02 | The company | `intro` *(add this id on the site)* | `pages/home` block `intro` (body, media = the clips set into the text) |
| HM-03 | The line | `factory` | block `factory` (heading, three captions as `items`) |
| HM-04 | Products list | `range` | block `range` + `families` (name, clip, hero) |
| HM-05 | Walk the floor | `floor` | block `floor` + `tour_chapters` (title, seconds) |
| HM-06 | What we do | `company` | block `company` (four services as `items`) + `leadership` (directors) |
| HM-07 | Request a quote | `contact` | block `contact` + `site` contacts |
| PR-01 | Products header | `top` | `pages/products` block `top` (counts come from `GET /v1/content/products`) |
| PR-02 | Browse by family | `families` *(add this id)* | `families` |
| PR-03 / PR-04 / PR-05 | Catalogue, quick view, tray | `catalogue` | `products` (cards and quick view) |
| PR-06 / PD-06 | Not listed? | `not-listed` *(add this id)* | block `not-listed` + `site.phones` |
| PD-01 … PD-05 | Product page (PG-03) | (page) | `GET /v1/content/products/{id}`: the product, its family, previous / next, four more from the family |
| AB-01 … AB-08 | About | `top`, `story`, `values`, `reach`, `chairman`, `leadership`, `what-we-do`, `visit` | `pages/about` blocks; `leadership`; `site` addresses and hours |
| QA-01 … QA-06 | Quality | `top`, `line`, `hygiene`, `documents`, `sustainability`, `see-it` *(add)* | `pages/quality` blocks; `stations`; `certificates`; `products` (QA-05: one dot per product) |
| CL-01 … CL-04 | Clients | `industries`, `clients`, `reviews`, `your-business` *(add)* | `industries`, `clients`, `stories`, `pages/clients` blocks |
| CT-01 … CT-03 | Contact | `enquiry`, `call`, `where` | `pages/contact` blocks; `site` phones, emails, address, map |

A block is `{section_id, design_id, kind, eyebrow, heading, body, media[], items[], buttons[], keywords[],
data{}, hidden}`. `items` are `{title, text, media, link, data}` (services, captions, values, steps). `data`
holds anything else a section needs. `keywords` are the words that lead the AI to this section.

### Products and their variants (D61)

A website product (`ac-8325`) is the **marketing record**. What Arifa actually sells, prices and stocks are its
**variants** in the CRM: the ERP items (8325 container 1*1000, 1*400; 45 cm foil in about 20 roll weights; retail
packs). Each variant is linked to its website product by the product's id. The website shows the product; the
assistant explains the variants (packing, availability as "in stock" / "limited" / "made to order", quantity
breaks) and the quotation carries the exact variant. **No variant data and no price is in this API.**

### Media

Every media field is a `MediaRef`: `{"asset_id": "<uuid>"}` from the media library, or `{"url": "https://…"}`
for an outside link (e.g. a YouTube film). The public API completes each one with `src` (absolute URL),
`alt` (in the requested language), `width`, `height` and `content_type`. Files live in Cloudflare R2 under
**content-addressed** URLs: a new file is a new URL, so cache them for ever (`Cache-Control: immutable`) and use
a `next/image` loader for resizing.

Upload (admin screens): `POST /v1/cms/media/uploads` with name, type, size and SHA-256. Then `PUT` the file to
the returned `upload_url` with the returned `headers`, straight from the browser to R2. Then
`POST /v1/cms/media/{id}/confirm`. Images are up to 15 MB, videos 300 MB, PDFs 30 MB and GLB 3D models 50 MB.

---

## 4. The revalidate call the website must accept

```
POST {website}/api/revalidate
Content-Type: application/json
X-Kx-Timestamp: <unix seconds>
X-Kx-Signature: sha256=<hex HMAC-SHA256(KX_APP_WEBSITE_REVALIDATE_SECRET, "<timestamp>." + raw body)>

{"event_id": "0195…", "collection": "products", "key": "ac-8325", "operation": "publish",
 "tags": ["products:ac-8325", "products", "manifest", "family:containers", "sitemap"],
 "paths": ["/products", "/ar/products", "/quality", "/ar/quality"]}
```

* Refuse a bad signature or a timestamp more than 5 minutes off (401).
* Call `revalidateTag(t)` for each tag and `revalidatePath(p)` for each path, then answer 200.
* A repeat of the same `event_id` is harmless.
* `operation: "unpublish"`: the document is gone; the same tags and paths are refreshed (the page then gets
  404 or drops the item).

Tag your fetches with the same names: `site`, `pages:<slug>`, `families`, `products`, `products:<id>`,
`family:<id>`, `manifest`, `sitemap`, `<collection>`. A Next.js route handler sketch is in FRONTEND_GUIDE.md.

---

## 5. Preview (draft mode)

`POST /v1/cms/preview-token` returns `{token, expires_at, url}`. The token is valid 30 minutes. The admin
screen opens `url` (`{website}/api/draft?token=…`); the site enables Next.js draft mode and adds
`?preview=<token>` to its content reads. The API then answers the drafts, never cached (`Cache-Control:
private, no-store`, `draft: true` on each document). An invalid or expired token is ignored: the published
content comes back.

---

## 6. Caching

Every `/v1/content` answer has `ETag` and `Cache-Control: public, max-age=30, s-maxage=60,
stale-while-revalidate=600`. Send `If-None-Match` to get `304`. The CMS revision (`revision` in list answers)
grows on every publish anywhere.

---

## 7. Errors

`application/problem+json` as in CRM_API.md §6.

| Status | When |
|---|---|
| 404 | The document is unknown or not published |
| 409 | A colleague saved the draft meanwhile: reload, then save again with the new `revision` |
| 422 `rule_violation` | `reason` is `cms_price` (a price in content), `cms_invalid` (a field breaks its schema; the message names it, e.g. `ar.blocks.2.heading`), `cms_family` (publish the family first), `cms_no_draft`, `cms_media_type` / `cms_media_size` |
| 401 / 403 | Admin routes without a staff token, or with a role that may not edit content |
| 503 | Media storage is not configured |
