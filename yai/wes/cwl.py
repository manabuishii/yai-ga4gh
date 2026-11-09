"""The CWL v1.2 subset accepted by the small built-in yai engine (wes.md section 1, D-014).

Only a single document of class: CommandLineTool is accepted. Anything else is 400 (CWLError).
No claim of "CWL conformance" (service-info tags state the subset).
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import Any

import yaml

from yai.common.imageref import BadImageReference, check_image_reference

DOC_KEYS = {"cwlVersion", "class", "id", "label", "doc", "baseCommand", "arguments", "inputs",
            "outputs", "stdout", "stderr", "requirements", "hints"}  # fmt: skip
INPUT_TYPES = {"File", "string", "int"}
# keys accepted at each level. Unknown keys are 400, not silently ignored
INPUT_KEYS = {"id", "type", "inputBinding", "default", "label", "doc"}
OUTPUT_KEYS = {"id", "type", "outputBinding", "label", "doc"}
DOCKER_KEYS = {"class", "dockerPull"}
WORKDIR = "/yai/work"
INPUT_ROOT = "/yai/inputs"
MAX_DEPTH = 64  # YAML nesting (well below Python's recursion limit)


class CWLError(Exception):
    pass


@dataclass(frozen=True)
class InputDef:
    name: str
    type: str
    default: Any = None
    position: int | None = None
    prefix: str | None = None


@dataclass(frozen=True)
class OutputDef:
    name: str
    glob: str


@dataclass
class Tool:
    base_command: list[str]
    arguments: list[str]
    inputs: list[InputDef]
    outputs: list[OutputDef]
    image: str
    stdout: str | None = None
    stderr: str | None = None
    label: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _named(items: Any, what: str) -> list[tuple[str, dict[str, Any]]]:
    """inputs and outputs may be either an array (with ids) or a map."""
    if isinstance(items, dict):
        return [(k, v if isinstance(v, dict) else {"type": v}) for k, v in items.items()]
    if isinstance(items, list):
        out = []
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("id"), str):
                raise CWLError(f"{what}: each item needs an id")
            out.append((it["id"].lstrip("#"), it))
        return out
    raise CWLError(f"{what} must be a list or a map")


def _plain_name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value or "/" in value or value in (".", ".."):
        raise CWLError(f"{what} must be a plain file name")
    if any(c in value for c in "*?[]{}$"):
        raise CWLError(f"{what}: wildcards and expressions are not supported")
    return value


class _Loader(yaml.SafeLoader):
    """Reject aliases (`*a`) and cap nesting depth (D-042).

    PyYAML builds aliases as references to the same object, so loading is fast, but walking or
    JSON-encoding later grows exponentially (140 MB from a doc under 1 KiB). Merge (`<<: *a`) needs
    an alias too, so it is stopped as well.
    compose_node is overridden, so the C version (CSafeLoader) is not used."""

    depth = 0

    def compose_node(self, parent, index):  # type: ignore[override]
        if self.check_event(yaml.AliasEvent):
            raise CWLError("YAML aliases are not supported")
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise CWLError(f"document nested deeper than {MAX_DEPTH}")
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1


def _load(text: bytes | str) -> dict[str, Any]:
    try:
        doc = yaml.load(text, Loader=_Loader)  # noqa: S506 a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise CWLError("not YAML/JSON") from exc
    if not isinstance(doc, dict):
        raise CWLError("document must be an object")
    if doc.get("cwlVersion") != "v1.2":
        raise CWLError("cwlVersion must be v1.2")
    return doc


def parse(text: bytes | str) -> Tool:
    doc = _load(text)
    if doc.get("class") != "CommandLineTool":
        raise CWLError("class must be CommandLineTool")
    return _tool(doc)


def _tool(doc: dict[str, Any]) -> Tool:
    if doc.get("class") != "CommandLineTool":
        raise CWLError("class must be CommandLineTool")
    unknown = set(doc) - DOC_KEYS
    if unknown:
        raise CWLError(f"unsupported fields: {sorted(unknown)}")  # $namespaces, $graph etc.

    base = doc.get("baseCommand")
    if isinstance(base, str):
        base = [base]
    if not isinstance(base, list) or not base or not all(isinstance(b, str) for b in base):
        raise CWLError("baseCommand must be a string or a list of strings")
    args = doc.get("arguments", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise CWLError("arguments must be a list of strings")
    if any("$(" in a or "${" in a for a in [*base, *args]):
        raise CWLError("expressions are not supported")

    label = doc.get("label")
    if label is not None and not isinstance(label, str):
        raise CWLError("label must be a string")
    reqs = doc.get("requirements", [])
    if isinstance(reqs, dict):
        if not all(v is None or isinstance(v, dict) for v in reqs.values()):
            raise CWLError("each requirement must be an object")
        reqs = [{"class": k, **(v or {})} for k, v in reqs.items()]
    if not isinstance(reqs, list):
        raise CWLError("requirements must be a list or a map")
    image = None
    for req in reqs:
        if not isinstance(req, dict) or req.get("class") != "DockerRequirement":
            name = req.get("class") if isinstance(req, dict) else req
            raise CWLError(f"unsupported requirement: {name}")
        if set(req) - DOCKER_KEYS:
            raise CWLError(f"DockerRequirement: only dockerPull is supported, not "
                           f"{sorted(set(req) - DOCKER_KEYS)}")  # fmt: skip
        image = req.get("dockerPull")
    if not isinstance(image, str) or not image:
        raise CWLError("DockerRequirement.dockerPull is required")
    try:
        check_image_reference(image)  # same check as TES, on submit (D-041)
    except BadImageReference as exc:
        raise CWLError(f"DockerRequirement.dockerPull: {exc}") from exc

    inputs = []
    for name, spec in _named(doc.get("inputs", []), "inputs"):
        if set(spec) - INPUT_KEYS:
            raise CWLError(f"input {name}: unsupported fields {sorted(set(spec) - INPUT_KEYS)}")
        typ = spec.get("type")
        if not isinstance(typ, str) or typ not in INPUT_TYPES:  # [string, "null"] etc. are 400 too
            raise CWLError(f"input {name}: type must be File, string or int")
        binding = spec.get("inputBinding")
        pos = prefix = None
        if binding is not None:
            if not isinstance(binding, dict) or set(binding) - {"position", "prefix"}:
                raise CWLError(f"input {name}: inputBinding supports position and prefix only")
            pos = binding.get("position", 0)
            prefix = binding.get("prefix")
            if not isinstance(pos, int) or (prefix is not None and not isinstance(prefix, str)):
                raise CWLError(f"input {name}: bad inputBinding")
        inputs.append(InputDef(name, typ, spec.get("default"), pos, prefix))

    outputs = []
    for name, spec in _named(doc.get("outputs", []), "outputs"):
        if set(spec) - OUTPUT_KEYS:
            raise CWLError(f"output {name}: unsupported fields {sorted(set(spec) - OUTPUT_KEYS)}")
        if spec.get("type") != "File":
            raise CWLError(f"output {name}: type must be File")
        binding = spec.get("outputBinding")
        if not isinstance(binding, dict) or set(binding) != {"glob"}:
            raise CWLError(f"output {name}: outputBinding.glob is required")
        outputs.append(OutputDef(name, _plain_name(binding["glob"], f"output {name} glob")))

    stdout = _plain_name(doc["stdout"], "stdout") if "stdout" in doc else None
    stderr = _plain_name(doc["stderr"], "stderr") if "stderr" in doc else None
    return Tool(base, list(args), inputs, outputs, image, stdout, stderr, label)


def bind(tool: Tool, params: dict[str, Any]) -> tuple[list[str], dict[str, tuple[str, str]]]:
    """Build argv and File inputs (name -> (location, container path)) (wes.md section 1)."""
    values: dict[str, Any] = {}
    files: dict[str, tuple[str, str]] = {}
    for inp in tool.inputs:
        value = params.get(inp.name, inp.default)
        if value is None:
            raise CWLError(f"input {inp.name} is required")
        if inp.type == "File":
            if not isinstance(value, dict) or value.get("class") != "File":
                raise CWLError(f"input {inp.name} must be a File object")
            loc = value.get("location") or value.get("path")
            if not isinstance(loc, str) or not loc:
                raise CWLError(f"input {inp.name}: location is required")
            basename = posixpath.basename(loc.split("?")[0].rstrip("/")) or inp.name
            basename = basename.split(":")[-1] or inp.name  # the drs://prefix:id form
            path = f"{INPUT_ROOT}/{inp.name}/{basename}"
            files[inp.name] = (loc, path)
            values[inp.name] = path
        elif inp.type == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise CWLError(f"input {inp.name} must be an int")
            values[inp.name] = str(value)
        else:
            if not isinstance(value, str):
                raise CWLError(f"input {inp.name} must be a string")
            values[inp.name] = value
    unknown = set(params) - {i.name for i in tool.inputs}
    if unknown:
        raise CWLError(f"unknown inputs: {sorted(unknown)}")

    # arguments and inputs with inputBinding in ascending position. On ties arguments come first,
    # inputs by name (wes.md section 1)
    items: list[tuple[int, int, str, list[str]]] = [
        (0, 0, f"{i:08d}", [a]) for i, a in enumerate(tool.arguments)
    ]
    for inp in tool.inputs:
        if inp.position is None:
            continue
        part = [inp.prefix, values[inp.name]] if inp.prefix else [values[inp.name]]
        items.append((inp.position, 1, inp.name, part))
    argv = list(tool.base_command)
    for _, _, _, part in sorted(items, key=lambda t: (t[0], t[1], t[2])):
        argv += part
    return argv, files


# --- serial Workflow (wes.md section 1, stage 2, D-033) ---
WORKFLOW_KEYS = {"cwlVersion", "class", "id", "label", "doc", "inputs", "outputs", "steps",
                 "requirements", "hints"}  # fmt: skip
WF_INPUT_KEYS = {"id", "type", "default", "label", "doc"}
WF_OUTPUT_KEYS = {"id", "type", "outputSource", "label", "doc"}
STEP_KEYS = {"id", "run", "in", "out", "label", "doc"}
STEP_PLACEHOLDER = "yai-step:"  # marker put in place of previous-step outputs during submit checks


@dataclass(frozen=True)
class Source:
    """Where a step input comes from: a workflow input, or an output of an earlier step (from 1)."""

    input: str | None = None
    step: int | None = None
    output: str | None = None


@dataclass
class StepDef:
    name: str
    tool: Tool
    sources: dict[str, Source]  # tool input name -> source (missing ones use the tool's default)


@dataclass
class Plan:
    """Execution plan. A single CommandLineTool is handled the same way as a one-step plan."""

    inputs: list[InputDef]
    steps: list[StepDef]
    outputs: dict[str, Source]  # run output name -> step output
    label: str | None = None
    is_workflow: bool = False


def parse_plan(text: bytes | str) -> Plan:
    """Read one CommandLineTool or one serial Workflow.

    Workflow subset: steps run one at a time in written order. Each step's run is an embedded
    CommandLineTool only (no references). `in` sources are workflow inputs or outputs of earlier
    steps only (forward references and cycles are 400). scatter, when, sub-Workflows and
    requirements are 400."""
    doc = _load(text)
    if doc.get("class") == "CommandLineTool":
        tool = _tool(doc)
        return Plan(
            inputs=[InputDef(i.name, i.type, i.default) for i in tool.inputs],
            steps=[StepDef("", tool, {i.name: Source(input=i.name) for i in tool.inputs})],
            outputs={o.name: Source(step=1, output=o.name) for o in tool.outputs},
            label=tool.label,
        )
    if doc.get("class") != "Workflow":
        raise CWLError("class must be CommandLineTool or Workflow")
    unknown = set(doc) - WORKFLOW_KEYS
    if unknown:
        raise CWLError(f"unsupported fields: {sorted(unknown)}")
    if doc.get("requirements"):
        raise CWLError("Workflow requirements are not supported (put them in each step's tool)")
    label = doc.get("label")
    if label is not None and not isinstance(label, str):
        raise CWLError("label must be a string")

    inputs = []
    for name, spec in _named(doc.get("inputs", []), "inputs"):
        if set(spec) - WF_INPUT_KEYS:
            raise CWLError(f"input {name}: unsupported fields {sorted(set(spec) - WF_INPUT_KEYS)}")
        if spec.get("type") not in INPUT_TYPES:
            raise CWLError(f"input {name}: type must be File, string or int")
        inputs.append(InputDef(name, spec["type"], spec.get("default")))
    wf_types = {i.name: i.type for i in inputs}

    steps: list[StepDef] = []
    out_types: dict[tuple[int, str], str] = {}  # (step, output name) -> type
    names: dict[str, int] = {}
    for name, spec in _named(doc.get("steps"), "steps"):
        if not name or name in names:
            raise CWLError(f"step {name!r}: empty or duplicate name")
        if set(spec) - STEP_KEYS:
            raise CWLError(f"step {name}: unsupported fields {sorted(set(spec) - STEP_KEYS)}")
        run = spec.get("run")
        if not isinstance(run, dict):
            raise CWLError(f"step {name}: run must be an embedded CommandLineTool")
        if run.get("cwlVersion", "v1.2") != "v1.2":
            raise CWLError(f"step {name}: cwlVersion must be v1.2")
        try:
            tool = _tool(run)
        except CWLError as exc:
            raise CWLError(f"step {name}: {exc}") from exc
        n = len(steps) + 1
        tool_types = {i.name: i.type for i in tool.inputs}
        sources: dict[str, Source] = {}
        ins = spec.get("in", {})
        if isinstance(ins, list):
            ins = {i.get("id"): i for i in ins if isinstance(i, dict)} if all(
                isinstance(i, dict) and isinstance(i.get("id"), str) for i in ins
            ) else None  # fmt: skip
        if not isinstance(ins, dict):
            raise CWLError(f"step {name}: in must be a map or a list with ids")
        for key, value in ins.items():
            if key not in tool_types:
                raise CWLError(f"step {name}: in {key} is not an input of the tool")
            src = value.get("source") if isinstance(value, dict) else value
            if isinstance(value, dict) and set(value) - {"id", "source"}:
                raise CWLError(f"step {name}: in {key} supports source only")
            if not isinstance(src, str) or not src:
                raise CWLError(f"step {name}: in {key} needs a source")
            src = src.lstrip("#")
            if "/" in src:
                step_name, _, out_name = src.partition("/")
                if step_name not in names:
                    raise CWLError(f"step {name}: in {key} refers to a later or unknown step")
                source = Source(step=names[step_name], output=out_name)
                typ = out_types.get((names[step_name], out_name))
            else:
                source = Source(input=src)
                typ = wf_types.get(src)
            if typ is None:
                raise CWLError(f"step {name}: in {key}: unknown source {src}")
            if typ != tool_types[key]:
                want = tool_types[key]
                raise CWLError(f"step {name}: in {key}: type {typ} does not match {want}")
            sources[key] = source
        missing = [i.name for i in tool.inputs if i.name not in sources and i.default is None]
        if missing:
            raise CWLError(f"step {name}: inputs without source or default: {missing}")
        outs = spec.get("out", [])
        if not isinstance(outs, list):
            raise CWLError(f"step {name}: out must be a list")
        tool_outs = {o.name for o in tool.outputs}
        for o in outs:
            oid = o.get("id") if isinstance(o, dict) else o
            if not isinstance(oid, str) or oid not in tool_outs:
                raise CWLError(f"step {name}: out {oid!r} is not an output of the tool")
            out_types[(n, oid)] = "File"
        names[name] = n
        steps.append(StepDef(name, tool, sources))
    if not steps:
        raise CWLError("Workflow needs at least one step")

    outputs: dict[str, Source] = {}
    for name, spec in _named(doc.get("outputs", []), "outputs"):
        if set(spec) - WF_OUTPUT_KEYS:
            extra = sorted(set(spec) - WF_OUTPUT_KEYS)
            raise CWLError(f"output {name}: unsupported fields {extra}")
        src = spec.get("outputSource")
        if spec.get("type") != "File" or not isinstance(src, str) or "/" not in src:
            raise CWLError(f"output {name}: type File and outputSource step/output are required")
        step_name, _, out_name = src.lstrip("#").partition("/")
        if (names.get(step_name), out_name) not in out_types:
            raise CWLError(f"output {name}: unknown outputSource {src}")
        outputs[name] = Source(step=names[step_name], output=out_name)
    return Plan(inputs, steps, outputs, label, is_workflow=True)


def check_params(plan: Plan, params: dict[str, Any]) -> dict[str, Any]:
    """Check workflow input values and return them with defaults filled (type rules as in bind)."""
    unknown = set(params) - {i.name for i in plan.inputs}
    if unknown:
        raise CWLError(f"unknown inputs: {sorted(unknown)}")
    values = {}
    for inp in plan.inputs:
        value = params.get(inp.name, inp.default)
        if value is None:
            raise CWLError(f"input {inp.name} is required")
        values[inp.name] = value
    # type checks are left to bind: build a one-step tool per input and pass it through
    probe = Tool([""], [], [InputDef(i.name, i.type) for i in plan.inputs], [], "-")
    bind(probe, values)
    return values


def step_params(
    plan: Plan, n: int, params: dict[str, Any], done: dict[int, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Values for the tool of step n (from 1). Earlier outputs from done (step -> name -> File).

    Outputs of steps not in done become marker (yai-step:) Files (for submit checks, not runs)."""
    values = check_params(plan, params)
    step = plan.steps[n - 1]
    out: dict[str, Any] = {}
    for key, src in step.sources.items():
        if src.input is not None:
            out[key] = values[src.input]
            continue
        assert src.step is not None and src.output is not None
        got = (done or {}).get(src.step, {}).get(src.output)
        if got is None:
            glob = next(o.glob for o in plan.steps[src.step - 1].tool.outputs
                        if o.name == src.output)  # fmt: skip
            got = {"class": "File", "location": f"{STEP_PLACEHOLDER}{src.step}/{glob}"}
        out[key] = got
    return out


def run_outputs(plan: Plan, done: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """run outputs (picked from each step's outputs by outputSource)."""
    return {
        name: done[src.step][src.output]
        for name, src in plan.outputs.items()
        if src.step is not None and src.output is not None and src.output in done.get(src.step, {})
    }
