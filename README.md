# yai — yet another implementation of GA4GH DRS, AAI (Passports), TES and WES

On GitHub and PyPI the name is `yai-ga4gh` (the repository and the Python distribution); the
import package and the command are `yai`. Repository: https://github.com/manabuishii/yai-ga4gh

A personal learning project: the GA4GH standards DRS 1.5.0, AAI 1.2 (with Passport 1.2), TES 1.1
and WES 1.1.0, implemented from the public specifications in Python (FastAPI).

- **yai-drs**: DRS 1.5.0 (read-only), with Passport-based access (Clearinghouse) and signed URLs
- **yai-aai**: a minimal broker / Passport issuer for tests; the Clearinghouse role is the main one
- **yai-tes**: TES 1.1 with a local-process runner and a Docker runner
- **yai-wes**: WES 1.1.0 with a small built-in engine for a subset of CWL v1.2
  (one CommandLineTool or a sequential Workflow), running each step as a TES task

It is a study of the specifications rather than a production service. The design notes, the
table of obligations taken from the specifications, and a list of gaps and inconsistencies found
in them are in `docs/` (partly in Japanese for now; translation is in progress).

## Quick start

```sh
uv sync            # creates .venv (Python 3.12 or later)
make check         # ruff, pyright, specification checks, tests, count of the covered obligations
YAI_CWLTOOL=<path to cwltool> make check   # also runs CWL-PARITY (skipped without cwltool)
.venv/bin/yai serve drs --config config/drs.example.yaml   # start one service on 127.0.0.1
.venv/bin/yai tes worker --config config/tes.example.yaml  # the TES runner (a separate process from the API)
```

The first `make check` downloads the pinned specification files (see below) into
`build/specs-ext/`, and pyright
(a dev dependency) fetches Node.js on first use; later runs need no network.

The four services together, on Docker compose: `deploy/README.md`. Interoperability tests with
other implementations (py-tes, Funnel, Sapporo, the GA4GH Passport Playground): `deploy/interop/`
and `tests/reports/interop/`.

## Layout

- Code: `yai/` (`common/` holds roles, error shapes, time, IDs and service-info; `aai/`, `drs/`,
  `tes/` and `wes/` are the services)
- Tests: `tests/` (each service runs in a separate process and is called over HTTP; responses are
  checked against the OpenAPI documents bundled in `build/`)
- Tools: `tools/ext_specs.py` (downloads and verifies the specifications),
  `tools/bundle_specs.py` (OpenAPI preprocessing), `tools/count_obligations.py`,
  `tools/conformance_drs.py` and `tools/conformance_tes.py` (run the official GA4GH conformance
  suites; results in `tests/reports/`), `tools/gen_diagram.py` (generates and checks the diagram
  and the list of specification gaps)
- Example configs: `config/*.example.yaml` (no secrets; `*.local.yaml` and `var/` are ignored by git)

## Specifications

This repository contains no copies of the specifications. `specs/SOURCES.yaml` pins every file
to a commit of its GitHub repository (GA4GH) or to a fixed URL (RFC Editor, OpenID Foundation),
and `specs/EXTERNAL.SHA256SUMS` holds the checksums; `tools/ext_specs.py` fetches them and fails
on any mismatch. See `specs/README.md`.

## License

Apache License 2.0, see `LICENSE`. The specifications and partner software referred to here are
the work of their authors and are not covered by this license.

The project was developed in a separate, private history; this repository starts from a single
commit.
