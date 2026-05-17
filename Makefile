COMPOSE_BASE = docker compose -f docker-compose.yml

.PHONY: dev dev-down dev-logs prod prod-down prod-logs test

dev:
	$(COMPOSE_BASE) up --build -d
	$(COMPOSE_BASE) exec app alembic upgrade head

dev-down:
	$(COMPOSE_BASE) down

dev-logs:
	$(COMPOSE_BASE) logs -f

prod:
	$(COMPOSE_BASE) up --build -d
	$(COMPOSE_BASE) exec app alembic upgrade head

prod-down:
	$(COMPOSE_BASE) down

prod-logs:
	$(COMPOSE_BASE) logs -f

test:
	$(COMPOSE_BASE) exec app pytest
