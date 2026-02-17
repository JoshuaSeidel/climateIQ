.PHONY: dev build up down logs test lint clean

dev:
	docker compose -f docker compose.yml -f docker compose.dev.yml up --build

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

test:
	cd backend && pytest -v --cov=. --cov-report=term-missing
	cd frontend && npm test

lint:
	cd backend && ruff check . && mypy .
	cd frontend && npm run lint

clean:
	docker compose down -v
	rm -rf backend/__pycache__ backend/.pytest_cache
	rm -rf frontend/node_modules frontend/dist
