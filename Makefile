.PHONY: install lint test dev dev-backend dev-frontend clean smoke capture-fixtures capture-phase4-fixtures

install:
	cd backend && uv sync
	cd frontend && pnpm install

lint:
	cd backend && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
	cd frontend && pnpm run lint

test:
	cd backend && uv run pytest -q
	cd frontend && pnpm run test -- --run

dev:
	@echo "Starting backend (uvicorn --workers 1) + frontend (vite) in parallel..."
	@echo "Backend: http://localhost:8710  |  Frontend: http://localhost:5710"
	@(trap 'kill 0' SIGINT; $(MAKE) -j 2 dev-backend dev-frontend)

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --workers 1 --host 0.0.0.0 --port 8710

dev-frontend:
	cd frontend && pnpm run dev

clean:
	rm -rf backend/.venv backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache
	rm -rf frontend/node_modules frontend/dist

smoke:
	@cd backend && (uv run pytest -m smoke -v; status=$$?; if [ $$status -eq 5 ]; then echo "(no smoke tests collected — OK)"; exit 0; else exit $$status; fi)

capture-fixtures:
	bash backend/scripts/capture_hermes_fixtures.sh

capture-phase4-fixtures: ## Re-capture Hermes v0.8 Phase 4 fixtures (manual — see Phase 4 Plan 01)
	@echo "Phase 4 Wave 0 fixtures must be captured manually on a Hermes box."
	@echo "See backend/tests/fixtures/hermes-cli/HERMES_V08_FINDINGS.md §'Re-capture instructions'"
	@echo "and .planning/phases/04-gateway-pairing-ws-core-value/04-01-PLAN.md Task 1 'how-to-verify'."
	@false

# Also update capture-fixtures script to skip overwriting if .hermes test profile is absent.
# The script as committed only re-captures profile_list_2_profiles.txt and gateway_pid_default.json;
# additional fixtures still require the manual probe-profile workflow documented in README.md.
