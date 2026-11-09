"""Serial Workflow (wes.md section 1, stage 2, D-033): subset parsing and step progression.

As in test_wes_engine.py, a fake TES is plugged in and the engine runs in-process.
The two-step test over HTTP (local process executor) is test_serial_workflow_two_steps in
test_wes.py.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.test_wes_engine import FakeTes, Rig, in_thread, until
from yai.wes import cwl

TWO_STEPS = """\
cwlVersion: v1.2
class: Workflow
inputs:
  word: string
outputs:
  final:
    type: File
    outputSource: second/out
steps:
  first:
    run:
      class: CommandLineTool
      baseCommand: echo
      inputs:
        w: {type: string, inputBinding: {position: 1}}
      outputs:
        out: {type: File, outputBinding: {glob: a.txt}}
      stdout: a.txt
      requirements: {DockerRequirement: {dockerPull: "alpine:3.20"}}
    in: {w: word}
    out: [out]
  second:
    run:
      class: CommandLineTool
      baseCommand: cat
      inputs:
        f: {type: File, inputBinding: {position: 1}}
      outputs:
        out: {type: File, outputBinding: {glob: a.txt}}
      stdout: a.txt
      requirements: {DockerRequirement: {dockerPull: "alpine:3.20"}}
    in: {f: first/out}
    out: [out]
"""


def test_two_steps_are_read_in_order():
    plan = cwl.parse_plan(TWO_STEPS)
    assert plan.is_workflow
    assert [s.name for s in plan.steps] == ["first", "second"]
    assert plan.steps[1].sources == {"f": cwl.Source(step=1, output="out")}
    assert plan.outputs == {"final": cwl.Source(step=2, output="out")}
    # At submission, a marker stands in for earlier steps' outputs, only to check argv can be built
    argv, files = cwl.bind(plan.steps[1].tool, cwl.step_params(plan, 2, {"word": "hi"}))
    assert argv == ["cat", "/yai/inputs/f/a.txt"]
    assert files["f"][0].startswith(cwl.STEP_PLACEHOLDER)


def test_command_line_tool_is_one_step():
    tool = TWO_STEPS.split("  first:\n    run:\n")[1].split("    in:")[0]
    tool = "cwlVersion: v1.2\n" + "\n".join(line[6:] for line in tool.splitlines())
    plan = cwl.parse_plan(tool)
    assert not plan.is_workflow and len(plan.steps) == 1
    assert plan.outputs == {"out": cwl.Source(step=1, output="out")}


def test_step_run_must_be_embedded():
    """Run references (another document) are rejected (wes.md section 1: "no run references")."""
    with pytest.raises(cwl.CWLError, match="embedded"):
        cwl.parse_plan(
            TWO_STEPS.replace(
                "  first:\n    run:\n", "  first:\n    run: echo.cwl\n    label:\n", 1
            )
        )


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (("in: {f: first/out}", "in: {f: third/out}"), "later or unknown"),
        (("in: {w: word}", "in: {w: second/out}"), "later or unknown"),  # forward reference
        (("in: {f: first/out}", "in: {f: word}"), "does not match"),  # string → File
        (("outputSource: second/out", "outputSource: second/nope"), "unknown outputSource"),
        (("    out: [out]\n  second:", "    out: [nope]\n  second:"), "not an output"),
        (("    in: {w: word}\n", "    in: {w: word}\n    scatter: w\n"), "unsupported fields"),
        (
            ("inputs:\n  word: string", "requirements: [{class: X}]\ninputs:\n  word: string"),
            "requirements",
        ),
        (("    in: {w: word}\n", "    in: {}\n"), "without source or default"),
    ],
)
def test_outside_the_workflow_subset_is_rejected(change, why):
    old, new = change
    assert old in TWO_STEPS
    with pytest.raises(cwl.CWLError, match=why):
        cwl.parse_plan(TWO_STEPS.replace(old, new, 1))


def test_docker_pull_must_be_an_image_reference():
    """D-041: dockerPull gets the same check as TES (leading `-` gives 400 at submission, never
    sent to TES)."""
    assert 'dockerPull: "alpine:3.20"' in TWO_STEPS
    for bad in ("--privileged", "alpine --privileged", "Alpine"):
        with pytest.raises(cwl.CWLError, match="dockerPull: image is not a valid"):
            cwl.parse_plan(TWO_STEPS.replace("alpine:3.20", bad))
    cwl.parse_plan(TWO_STEPS.replace("alpine:3.20", "ghcr.io/x/y:1.0@sha256:" + "ab" * 16))


def test_unknown_workflow_input_is_rejected():
    plan = cwl.parse_plan(TWO_STEPS)
    with pytest.raises(cwl.CWLError, match="unknown inputs"):
        cwl.step_params(plan, 1, {"word": "x", "extra": 1})
    with pytest.raises(cwl.CWLError, match="must be a string"):
        cwl.step_params(plan, 1, {"word": 3})


# --- Engine: steps in order, each step's outputs feed the next step's inputs ---


class OutputsTes(FakeTes):
    """Fake that remembers submitted tasks and returns outputs and tesTaskLog.outputs on GetTask."""

    def __init__(self, clock):
        super().__init__(clock)
        self.sent: dict[str, dict[str, Any]] = {}

    def create(self, task: dict[str, Any]) -> str:
        task_id = super().create(task)
        self.sent[task_id] = task
        return task_id

    def get(self, task_id: str) -> dict[str, Any]:
        out = super().get(task_id)
        task = self.sent[task_id]
        out["outputs"] = task["outputs"]
        out["executors"] = task["executors"]
        out["logs"] = [{"logs": [{"exit_code": 0}],
                        "outputs": [{"path": o["path"], "url": o["url"], "size_bytes": "3"}
                                    for o in task["outputs"]]}]  # fmt: skip
        return out


@pytest.fixture
def wrig(tmp_path) -> Rig:
    rig = Rig(tmp_path)
    rig.tes = OutputsTes(rig.clock)
    (tmp_path / "tool.cwl").write_text(TWO_STEPS)
    rig.engine = rig.new_engine()
    return rig


def two_step_run(rig: Rig, run_id: str = "rw") -> str:
    req = {"workflow_params": {"word": "hi"}}
    rig.db.create_run(run_id, ("iss", "sub"), req, rig.clock.now(), False,
                      [("first", []), ("second", [])])  # fmt: skip
    return run_id


def finish_task(rig: Rig, n: int, state: str = "COMPLETE") -> None:
    tes = rig.tes
    assert isinstance(tes, OutputsTes)
    until(lambda: len(tes.sent) >= n)
    rig.tes.tasks[f"t{n}"]["state"] = state


def test_steps_run_in_order_and_pass_file_urls(wrig):
    run_id = two_step_run(wrig)
    t = in_thread(wrig.engine.drive, run_id)
    finish_task(wrig, 1)
    finish_task(wrig, 2)
    t.join(5)
    assert wrig.state(run_id) == "COMPLETE"
    first, second = wrig.tes.sent["t1"], wrig.tes.sent["t2"]
    assert first["name"] == f"{run_id}.1" and second["name"] == f"{run_id}.2"
    # Step 2 input is exactly step 1's file:// output (C-25 handoff within one run)
    assert second["inputs"][0]["url"] == first["outputs"][0]["url"]
    assert "/wes/rw/1/out/" in first["outputs"][0]["url"]
    assert "/wes/rw/2/out/" in second["outputs"][0]["url"]  # same output name, no clash
    run = wrig.db.get_run(run_id)
    assert run is not None and run.outputs is not None
    assert run.outputs["final"]["location"] == second["outputs"][0]["url"]
    assert [s.cmd for s in wrig.db.steps(run_id)] == [["echo", "hi"],
                                                      ["cat", "/yai/inputs/f/a.txt"]]  # fmt: skip


def test_failed_first_step_does_not_submit_the_second(wrig):
    run_id = two_step_run(wrig)
    t = in_thread(wrig.engine.drive, run_id)
    finish_task(wrig, 1, "EXECUTOR_ERROR")
    t.join(5)
    assert wrig.state(run_id) == "EXECUTOR_ERROR"
    assert list(wrig.tes.sent) == ["t1"]


def test_cancel_between_steps_stops_the_running_one_and_submits_no_more(wrig):
    run_id = two_step_run(wrig)
    t = in_thread(wrig.engine.drive, run_id)
    finish_task(wrig, 1)
    until(lambda: len(wrig.tes.sent) == 2)
    assert wrig.db.transition(run_id, "RUNNING", "CANCELING", wrig.clock.now())
    t.join(5)
    assert wrig.state(run_id) == "CANCELED"
    assert wrig.tes.cancels == ["t2"]  # not sent to the finished step 1
    assert list(wrig.tes.sent) == ["t1", "t2"]


def test_restart_after_first_step_continues_with_the_second(wrig):
    run_id = two_step_run(wrig)
    t = in_thread(wrig.engine.drive, run_id)
    finish_task(wrig, 1)
    until(lambda: len(wrig.tes.sent) == 2)
    # Pretend a crash and rebuild on the same DB (step 1 output recorded, step 2 submitted)
    engine = wrig.restart()
    t2 = in_thread(engine.drive, run_id)
    wrig.tes.tasks["t2"]["state"] = "COMPLETE"
    t.join(5)
    t2.join(5)
    assert wrig.state(run_id) == "COMPLETE"
    assert len(wrig.tes.sent) == 2  # step 1 is not resubmitted


def test_lost_passport_fails_only_the_step_that_needs_it(wrig):
    """E2E-09 reading (D-033): a Passport is needed only when step 2 has a drs://."""
    doc = TWO_STEPS.replace("in: {f: first/out}", "in: {f: data}").replace(
        "inputs:\n  word: string", "inputs:\n  word: string\n  data: File"
    )
    (wrig.tmp / "tool.cwl").write_text(doc)
    req = {
        "workflow_params": {
            "word": "hi",
            "data": {"class": "File", "location": "drs://drs.yai.test/o1"},
        }
    }
    wrig.db.create_run("rp", ("iss", "sub"), req, wrig.clock.now(), True,
                       [("first", []), ("second", [])])  # fmt: skip
    engine = wrig.restart()  # no passports (after restart)
    t = in_thread(engine.drive, "rp")
    finish_task(wrig, 1)  # step 1 has no drs://, so it can be submitted
    t.join(5)
    run = wrig.db.get_run("rp")
    assert run is not None and run.state == "SYSTEM_ERROR"
    assert "credentials were lost" in json.dumps(run.system_logs)
    assert list(wrig.tes.sent) == ["t1"]


def test_NEG_YAML_ALIAS():
    """Aliases (including merge `<<`) give 400. PyYAML builds them by reference,
    so loading is fast, but traversal or JSON conversion grows exponentially (~500 chars -> 140 MB).
    Deep nesting also gives 400, not 500."""
    bomb = ["a0: &a0 [x]"] + [f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 9)}]" for i in range(1, 9)]
    tool = "\n".join(bomb) + "\n" + TWO_STEPS  # stops at load time, before the key checks
    for doc in (tool, "a: &a {x: 1}\nb: {<<: *a}\n", "cwlVersion: v1.2\ndefault: &d 1\nx: *d\n"):
        with pytest.raises(cwl.CWLError, match="aliases are not supported"):
            cwl.parse_plan(doc)
    for deep in ("[" * 100_000, "a:\n" + "".join(" " * i + "- \n" for i in range(1, 200))):
        with pytest.raises(cwl.CWLError):
            cwl.parse_plan(deep)
    cwl.parse_plan(TWO_STEPS)  # a normal document without anchors works as before
