"""Bundle the OpenAPI files under specs/ into one file per API, readable by the schema checkers.

This is the only place that preprocesses them (language.md section 3, spec-pins.md section 2):
- External `$ref` (the raw URL of service-info v1.0.0) is pointed at
  build/specs-ext/service-info/service-info.yaml (downloaded by tools/ext_specs.py)
- Relative-path `$ref` (the split DRS files) is copied under `x-yai-bundled` in the root document
  and becomes an internal `$ref` (kept as a reference, not inlined, because some schemas such as
  DRS ContentsObject refer to themselves)
- `$ref` to a Markdown description (DRS tags) is replaced by that file's text
- Integer response-code keys in TES (`200:`) become strings (G-TES-11)
- Dates in example values (which YAML reads as dates) become strings
- `servers` is reduced to the path only (`/ga4gh/<api>/v1`) for the checks

Usage: python tools/bundle_specs.py   -> build/{drs,tes,wes}.openapi.json
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import ext_specs
import yaml

ROOT = Path(__file__).resolve().parent.parent
SPECS = ext_specs.EXT  # the pinned originals (specs/SOURCES.yaml), downloaded by ext_specs
BUILD = ROOT / "build"

SERVICE_INFO_URL = (
    "https://raw.githubusercontent.com/ga4gh-discovery/ga4gh-service-info/v1.0.0/service-info.yaml"
)
REMOTE = {SERVICE_INFO_URL: SPECS / "service-info" / "service-info.yaml"}
SOURCES = {
    "drs": (SPECS / "drs" / "openapi" / "data_repository_service.openapi.yaml", "/ga4gh/drs/v1"),
    "tes": (SPECS / "tes" / "task_execution_service.openapi.yaml", "/ga4gh/tes/v1"),
    "wes": (SPECS / "wes" / "workflow_execution_service.openapi.yaml", "/ga4gh/wes/v1"),
}


class Bundler:
    def __init__(self, root_file: Path):
        self.root_file = root_file.resolve()
        self.cache: dict[Path, Any] = {}
        self.bundled: dict[str, Any] = {}

    def load(self, path: Path) -> Any:
        path = path.resolve()
        if path not in self.cache:
            with open(path, encoding="utf-8") as f:
                self.cache[path] = yaml.safe_load(f)
        return self.cache[path]

    def target(self, ref: str, base: Path) -> tuple[Path, str]:
        file_part, _, pointer = ref.partition("#")
        if file_part in REMOTE:
            path = REMOTE[file_part]
        elif file_part == "":
            path = base
        elif "://" in file_part:
            raise ValueError(f"unknown remote $ref: {ref}")
        else:
            path = (base.parent / file_part).resolve()
        return path.resolve(), pointer

    @staticmethod
    def resolve_pointer(doc: Any, pointer: str) -> Any:
        node = doc
        for part in [p for p in pointer.split("/") if p]:
            part = part.replace("~1", "/").replace("~0", "~")
            node = node[part]
        return node

    def key(self, path: Path, pointer: str) -> str:
        rel = path.relative_to(SPECS).as_posix()
        return re.sub(r"[^A-Za-z0-9_.-]", "_", f"{rel}{pointer}")

    def walk(self, node: Any, base: Path) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and isinstance(node["$ref"], str):
                ref = node["$ref"]
                path, pointer = self.target(ref, base)
                if path.suffix == ".md":
                    return path.read_text(encoding="utf-8")
                if path == self.root_file:
                    return {"$ref": "#" + pointer}
                k = self.key(path, pointer)
                if k not in self.bundled:
                    self.bundled[k] = None  # placeholder for self-referencing schemas
                    target = self.resolve_pointer(self.load(path), pointer)
                    self.bundled[k] = self.walk(target, path)
                return {"$ref": f"#/x-yai-bundled/{k}"}
            return {str(k): self.walk(v, base) for k, v in node.items()}
        if isinstance(node, list):
            return [self.walk(v, base) for v in node]
        if isinstance(node, date):  # YAML reads dates in example values as dates
            return node.isoformat()
        return node

    def bundle(self, server: str) -> dict[str, Any]:
        doc = self.walk(self.load(self.root_file), self.root_file)
        doc["servers"] = [{"url": server}]
        if self.bundled:
            doc["x-yai-bundled"] = self.bundled
        return doc


def bundle(api: str) -> dict[str, Any]:
    path, server = SOURCES[api]
    return Bundler(path).bundle(server)


def main() -> int:
    ext_specs.require("bundle_specs")
    BUILD.mkdir(exist_ok=True)
    for api in SOURCES:
        out = BUILD / f"{api}.openapi.json"
        out.write_text(json.dumps(bundle(api), ensure_ascii=False, indent=1), encoding="utf-8")
        print(out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
