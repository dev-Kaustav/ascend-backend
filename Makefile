COMPOSE_BASE = docker compose -f docker-compose.yml

.PHONY: dev dev-down dev-logs prod prod-down prod-logs pull test configure-profile purge-dry purge

# --ff-only, not a plain pull: on a server a divergent history should stop and be looked at,
# not silently produce a merge commit nobody reviewed.
pull:
	git pull --ff-only

dev: pull
	$(COMPOSE_BASE) up --build -d
	$(COMPOSE_BASE) exec app alembic upgrade head

dev-down:
	$(COMPOSE_BASE) down

dev-logs:
	$(COMPOSE_BASE) logs -f

prod: pull
	$(COMPOSE_BASE) up --build -d
	$(COMPOSE_BASE) exec app alembic upgrade head

prod-down:
	$(COMPOSE_BASE) down

prod-logs:
	$(COMPOSE_BASE) logs -f

test:
	$(COMPOSE_BASE) exec app pytest

# --- one-off data operations -------------------------------------------------
# Both scripts are dry-run by default; the targets below mirror that.

configure-profile:
	$(COMPOSE_BASE) exec app python scripts/configure_company_profile.py \
		--config scripts/company_profile.json --qr-image scripts/qr.png

configure-profile-apply:
	$(COMPOSE_BASE) exec app python scripts/configure_company_profile.py \
		--config scripts/company_profile.json --qr-image scripts/qr.png --apply

purge-dry:
	$(COMPOSE_BASE) exec app python scripts/purge_transactional_data.py --inventory restore

# Deletes every order and invoice. Prompts for a typed confirmation; take a pg_dump first.
purge:
	$(COMPOSE_BASE) exec app python scripts/purge_transactional_data.py --inventory restore --apply
