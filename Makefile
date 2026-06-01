SHELL  := /bin/bash
CONDA  := eval "$$(conda shell.bash hook)" && conda activate ai-clinical-triage &&

# ─────────────────────────────────────────────────────────────────────────────
# Local dev (no Docker)
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: install
install:
	$(CONDA) pip install -r requirements.txt

.PHONY: run
run:
	@[ -f .env ] || (echo "ERROR: .env not found — run: cp .env.example .env" && exit 1)
	$(CONDA) python main.py

.PHONY: migrate
migrate:
	$(CONDA) alembic upgrade head

.PHONY: migrate-down
migrate-down:
	$(CONDA) alembic downgrade -1

.PHONY: admin
admin:
	$(CONDA) python scripts/create_admin.py

.PHONY: dtmf
dtmf:
	$(CONDA) python scripts/generate_dtmf_audio.py

.PHONY: test
test:
	$(CONDA) python -m pytest tests/ -q --tb=short

.PHONY: test-unit
test-unit:
	$(CONDA) python -m pytest tests/unit/ -q --tb=short

.PHONY: test-integration
test-integration:
	$(CONDA) python -m pytest tests/integration/ -q --tb=short

.PHONY: test-multilingual
test-multilingual:
	$(CONDA) python -m pytest tests/multilingual/ -q --tb=short

# ─────────────────────────────────────────────────────────────────────────────
# Docker Compose
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: up
up:
	@[ -f .env ] || (echo "ERROR: .env not found — run: cp .env.example .env" && exit 1)
	docker compose up --build -d

.PHONY: down
down:
	docker compose down

.PHONY: down-v
down-v:
	docker compose down -v

.PHONY: logs
logs:
	docker compose logs -f api

.PHONY: ps
ps:
	docker compose ps

.PHONY: db-migrate
db-migrate:
	docker compose exec api alembic upgrade head

.PHONY: db-admin
db-admin:
	docker compose exec api python scripts/create_admin.py

.PHONY: db-backup
db-backup:
	@mkdir -p backups
	docker compose exec db pg_dump -U ai_user ai_receptionist \
	  | gzip > backups/backup_$$(date +%Y%m%d_%H%M%S).sql.gz
	@echo "Backup saved to backups/"

# ─────────────────────────────────────────────────────────────────────────────
# Infra only (useful when running app locally but needing DB+Redis)
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: infra
infra:
	docker compose up db redis -d

.PHONY: infra-down
infra-down:
	docker compose stop db redis

# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: health
health:
	curl -s http://localhost:8000/api/v1/health | python -m json.tool

.PHONY: help
help:
	@echo ""
	@echo "AI Medical Receptionist — Make targets"
	@echo ""
	@echo "  LOCAL DEV"
	@echo "    make install         Install Python deps into conda env"
	@echo "    make run             Start FastAPI server (requires .env + DB + Redis)"
	@echo "    make migrate         Run Alembic migrations"
	@echo "    make admin           Create first admin user"
	@echo "    make dtmf            Generate DTMF audio files (requires AZURE_SPEECH_KEY)"
	@echo "    make test            Run full test suite"
	@echo "    make infra           Start only DB + Redis in Docker"
	@echo ""
	@echo "  DOCKER COMPOSE"
	@echo "    make up              Build + start all services"
	@echo "    make down            Stop all services"
	@echo "    make logs            Follow API logs"
	@echo "    make db-migrate      Run migrations inside container"
	@echo "    make db-admin        Create first admin inside container"
	@echo "    make db-backup       Backup PostgreSQL to backups/"
	@echo ""
	@echo "  OTHER"
	@echo "    make health          Check /api/v1/health endpoint"
	@echo ""
