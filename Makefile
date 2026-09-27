# Local only. Never deploy from this Makefile. Render tracks master.
.PHONY: setup dev e2e e2e-live

setup:
	python3 -m venv .venv
	.venv/bin/pip install -q -e .
	test -f .env || cp .env.example .env
	cd web && npm install && npm run build

dev:
	./scripts/dev.sh

e2e:
	.venv/bin/python -m pytest tests/test_e2e_local.py -q

e2e-live:
	.venv/bin/python -m pytest tests/test_e2e_local.py -q --live
