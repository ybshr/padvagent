SHELL := /bin/bash
COMPOSE := docker compose
PORT ?= 8110

.PHONY: install dev run test demo chat down

install:
	cd api && poetry install

dev:            ## run API in Docker on :8110
	@$(COMPOSE) up --build -d api

run:            ## run API locally with reload on :$(PORT) → http://localhost:$(PORT)/chat
	cd api && PORT=$(PORT) ./dev_start.sh

test:           ## offline tests (fake LLM + fake Odoo)
	cd api && poetry run pytest -q

demo:           ## run test cases A, B, C against OpenAI + Odoo
	cd api && poetry run python -m src.cli --scenario all

chat:           ## interactive terminal chat
	cd api && poetry run python -m src.cli

down:
	@$(COMPOSE) down
