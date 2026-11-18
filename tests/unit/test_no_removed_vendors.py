"""D42 guard: only the server plan v2 §2 platforms live in the code. A vendor removed on 16 Nov must not come back
by accident (an import, a settings class, a default order). Docs may still mention them as history."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [
    p
    for p in (
        *ROOT.glob("packages/*/src/**/*.py"),
        *ROOT.glob("apps/*/src/**/*.py"),
        *ROOT.glob("services/**/*.py"),
    )
    if "migrations" not in p.parts  # revisions are history: they name the providers rows used to have
]
REMOVED = {
    "twilio": r"\btwilio\b",
    "postmark": r"\bpostmark\b",
    "soniox": r"\bsoniox\b",
    "google chirp": r"\bchirp\b",
    "gmail api": r"gmail[ _]?api|googleapis\.com/gmail",  # Gmail as a mail client in quote-stripping is fine
    "sentry sdk": r"\bsentry_sdk\b|\bsentry_dsn\b",
    "better stack": r"\bbetterstack\b",
    "parameter store": r"\bssm\b|get_parameter\(",
    "sns": r"\bSnsVerifier\b|\bsns_topic",
    "s3 kms": r"aws:kms|SSEKMSKeyId",
    "cloudwatch emf": r"CloudWatchMetrics|\bemf_line\b",
    "localstack": r"\blocalstack\b",
}


@pytest.mark.parametrize("vendor", sorted(REMOVED))
def test_removed_vendor_is_not_in_the_code(vendor: str) -> None:
    pattern = re.compile(REMOVED[vendor], re.IGNORECASE)
    hits = [
        f"{path.relative_to(ROOT)}:{n}"
        for path in SOURCES
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line) and "removed" not in line.lower()
    ]
    assert not hits, f"{vendor} is back in the code: {hits[:5]}"


def test_no_terraform_or_aws_workflows_remain() -> None:
    assert not list((ROOT / "infra").glob("**/*.tf"))
    paths = list((ROOT / ".github/workflows").glob("*.yml"))
    # image-scan.yml: the weekly Trivy scan of the published images (infra review M16, 19 Nov), no deploy;
    # feature-map.yml: ADR-0001 Phase 0 checks docs/FEATURE_MAP.md (no cloud, no deploy)
    assert {p.name for p in paths} == {"ci.yml", "release.yml", "image-scan.yml", "feature-map.yml"}
    cloud = re.compile(r"aws-actions/|hashicorp/setup-terraform|\bterraform (plan|apply)\b", re.IGNORECASE)
    assert not [p.name for p in paths if cloud.search(p.read_text(encoding="utf-8"))]
