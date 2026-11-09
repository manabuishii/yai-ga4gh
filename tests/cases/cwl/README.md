# CWL-PARITY examples (test-plan.md section 4)

Runs the same examples on the yai built-in engine (the subset in `design/wes.md` section 1) and on
cwltool, and compares the output contents.

- `<name>.cwl` and `<name>.job.json` form a pair. A relative `location` in the job refers to a file
  in this directory (passed to yai as an attachment)
- File paths differ per implementation (yai uses `/yai/inputs/<input name>/<name>`, cwltool a
  temporary directory), so no example puts a path in its output
- cwltool runs with `--no-container` (the work container has no Docker, D-024). The tests run only
  when the environment variable `YAI_CWLTOOL` points to cwltool (otherwise 2 tests are skipped)
- With `--no-container`, cwltool rejects a DockerRequirement under `requirements`, so the tests move
  it to `hints` only in the copy given to cwltool (the yai subset requires it under `requirements`)

Installation (checked on 2026-10-16 with cwltool 3.3.20260925135507):

```sh
uv venv -p 3.12 /path/to/cwl-venv
uv pip install --python /path/to/cwl-venv cwltool
YAI_CWLTOOL=/path/to/cwl-venv/bin/cwltool make check
```
