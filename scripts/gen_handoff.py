"""Build the whole hand-off pack (docs/handoff/, D60) from the code, offline (no database, no network).

    uv run python scripts/gen_handoff.py          # or: make handoff

It writes, for the frontend developer and the QA tester:

* CRM_API.md, CMS_API.md, WEBSITE_API.md: a hand-written design head (scripts/handoff_heads/) + a reference
  generated per endpoint (who may call it, parameters, request / response schema tables, JSON examples) + a
  data-shapes appendix (scripts/gen_frontend_docs.py);
* openapi/openapi-*.json for a mock server;
* postman/: one end-to-end collection, one collection per package (CRM, CMS, Website, Automation) and the
  local / staging environments (scripts/gen_tester_pack.py);
* endpoints.json, read by the two Word guides (docs/handoff/build/*.js, built with node, see README.md);
* vectors/site-nav.json: the site-navigation cases the backend's instant resolver passes (D57), for the
  website's own optimistic resolver (copied from kx/agents/evals/site_nav/cases.json).

CI regenerates the pack and fails when the committed files differ (.github/workflows/handoff.yml).
"""

from __future__ import annotations

import pathlib
import shutil
import sys

import gen_frontend_docs
import gen_tester_pack

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "handoff"
SITE_NAV = ROOT / "packages/kx-agent-framework/src/kx/agents/evals/site_nav/cases.json"


def main(out: pathlib.Path = OUT) -> None:
    gen_tester_pack.main(out)
    gen_frontend_docs.main(out)
    (out / "vectors").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SITE_NAV, out / "vectors" / "site-nav.json")
    print("hand-off pack written to", out)


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
