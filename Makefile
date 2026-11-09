PY := .venv/bin/python

.PHONY: check lint typecheck test specs obligations e2e-compose

check: lint typecheck specs test obligations

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

typecheck:
	.venv/bin/pyright

specs:
	$(PY) tools/ext_specs.py
	$(PY) tools/bundle_specs.py
	$(PY) tools/gen_obligations.py --check
	$(PY) tools/gen_diagram.py --check
	$(PY) tools/interop_diagram_check.py

test:
	$(PY) -m pytest -q --junitxml=build/junit.xml

obligations:
	$(PY) tools/count_obligations.py build/junit.xml
	$(PY) tools/test_status.py build/junit.xml build/junit-compose.xml

# The combined tests on compose (needs a Docker daemon; not part of make check). Results: build/junit-compose.xml
e2e-compose:
	sh tools/compose_e2e.sh
