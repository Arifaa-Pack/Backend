# Developer shortcuts. CI runs the same commands (.github/workflows/ci.yml).
.DEFAULT_GOAL := help
SHELL := /bin/bash

IMAGE     ?= kairox/app:dev

.PHONY: help
help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-16s %s\n", $$1, $$2}'

.PHONY: sync
sync: ## Install the workspace (dev deps included)
	uv sync --frozen

.PHONY: lint
lint: ## ruff + mypy + import-linter
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy packages/*/src apps/*/src scripts
	uv run lint-imports

.PHONY: test
test: ## pytest (DB tests need KX_TEST_PG_DSN, e.g. make up then KX_TEST_PG_DSN=postgresql://postgres:postgres@localhost:5432/postgres)
	uv run pytest

.PHONY: test-spec
test-spec: ## spec suites only (pricing, price guard): fast, no DB
	uv run pytest tests/spec -q

.PHONY: bench
bench: ## speed benchmarks of the hot paths
	uv run pytest tests/spec -m bench --benchmark-enable --benchmark-columns=min,mean,ops

.PHONY: migrate
migrate: ## apply Alembic migrations to every DB (the job queue is a table in the automation revisions)
	uv run kx migrate

.PHONY: handoff
handoff: ## regenerate docs/handoff/ (API docs, OpenAPI, Postman collections, endpoints.json) from the code
	uv run python scripts/gen_handoff.py

.PHONY: api
api: ## run the API locally on :8000
	uv run kx api

.PHONY: worker
worker: ## run the Celery worker locally (D25)
	uv run kx worker --pool solo

.PHONY: beat
beat: ## run the one Celery Beat locally (D25)
	uv run kx beat

.PHONY: up
up: ## Start postgres + redis (local DBs are prefixed pack_, like the server)
	docker compose up -d postgres redis

.PHONY: up-all
up-all: ## Build and start migrate + api + worker + beat as well
	docker compose up --build -d

.PHONY: up-voice
up-voice: ## LiveKit (dev) + the free speech server + the voice worker (free version)
	docker compose --profile voice up --build -d livekit speech voice

.PHONY: up-mail
up-mail: ## Stalwart v1.1.1 (finish its web setup once on :8090, login admin / local-dev-only)
	docker compose --profile mail up -d stalwart

.PHONY: up-obs
up-obs: ## Phoenix (AI audit trail) on :6006
	docker compose --profile obs up -d phoenix

.PHONY: up-embed
up-embed: ## Text Embeddings Inference (Qwen3 locally; run alone on a small laptop)
	docker compose --profile embed up -d embeddings

.PHONY: platform
platform: ## Show the resolved paid / free version per field (D41)
	uv run kx platform show

.PHONY: stacks-check
stacks-check: ## docker compose config of every server stack (dummy env, the same values as CI)
	cd infra/server/stacks && export KX_ENV_FILE=env/pack.env.example KX_CMS_ENV_FILE=env/pack-cms.env.example \
	  POSTGRES_PASSWORD=x R2_BUCKET=x R2_ENDPOINT_URL=https://x R2_ACCESS_KEY_ID=x R2_SECRET_ACCESS_KEY=x \
	  LIVEKIT_NODE_IP=203.0.113.1 LIVEKIT_API_KEY=x LIVEKIT_API_SECRET=x PHOENIX_DB_PASSWORD=x \
	  CROWDSEC_CF_BOUNCER_KEY=x CROWDSEC_FW_BOUNCER_KEY=x CLOUDFLARE_API_TOKEN=x PUBLIC_API_URL=https://x \
	  REDIS_PASSWORD=x WALG_LIBSODIUM_KEY=x PHOENIX_SECRET=x KX_SES_SMTP_PASSWORD=x \
	  KX_APP_IMAGE=ghcr.io/x/app:ci KX_CMS_IMAGE=ghcr.io/x/cms:ci && \
	for f in compose.*.yml; do echo "== $$f"; docker compose -f $$f --profile selfhost-web config -q || exit 1; done

.PHONY: down
down: ## Stop the stack and DROP local data
	docker compose down -v

.PHONY: image
image: ## Build the amd64 app image (the dedicated server is x86_64)
	docker buildx build --platform linux/amd64 -t $(IMAGE) --load .

.PHONY: image-speech
image-speech: ## Build the free speech server image
	docker buildx build --platform linux/amd64 -t kairox/speech:dev --load services/speech

.PHONY: lint-ci
lint-ci: ## actionlint + hadolint
	uvx --from actionlint-py actionlint
	uvx --from hadolint-bin hadolint Dockerfile services/speech/Dockerfile infra/server/postgres/Dockerfile
