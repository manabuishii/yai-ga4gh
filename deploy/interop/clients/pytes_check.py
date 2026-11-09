"""INTEROP-TES-1: partner client py-tes 1.1.4 -> yai-tes (https://tes.yai.test, local
CA, alice's access token).

Checks (test-plan.md section 6): create, get, list and cancel parse as py-tes types; views and
paging.
py-tes tries /ga4gh/tes/v1, /v1, / after the url in order (next one on 404/5xx).
"""

from __future__ import annotations

import time

import tes  # py-tes
from common import Report, access_token, excerpt

TES_URL = "https://tes.yai.test"


def main() -> int:
    rep = Report("INTEROP-TES-1", "py-tes 1.1.4 (PyPI) → yai-tes")
    token = access_token("alice", "yai-tes")
    cli = tes.HTTPClient(TES_URL, timeout=30, token=token)
    rep.doc["client_urls_tried"] = cli.urls

    # service-info (parses as py-tes ServiceInfo)
    try:
        si = cli.get_service_info()
        rep.step(
            "Parse service-info as py-tes type",
            True,
            excerpt=excerpt(si.as_dict() if hasattr(si, "as_dict") else si),
        )
    except Exception as exc:
        rep.step("Parse service-info as py-tes type", False, error=repr(exc))

    # Create (also with 1.1 fields backend_parameters, ignore_error)
    task = tes.Task(
        name="interop-tes-1 echo",
        description="py-tes 1.1.4 → yai-tes",
        executors=[
            tes.Executor(
                image="alpine:3.20",
                command=["sh", "-c", "echo hello from py-tes"],
                ignore_error=False,
            )
        ],
        resources=tes.Resources(
            cpu_cores=1, ram_gb=0.5, backend_parameters={"yai.note": "interop"}
        ),
        tags={"interop": "tes-1"},
    )
    try:
        created = cli.create_task(
            task
        )  # py-tes 1.1.4 returns the id string, not CreateTaskResponse
        tid = created if isinstance(created, str) else created.id
        rep.step("CreateTask (with 1.1 fields)", True, task_id=tid)
    except Exception as exc:
        rep.step("CreateTask (with 1.1 fields)", False, error=repr(exc))
        return rep.save("INTEROP-TES-1.json")

    # The 3 GetTask views
    for view in ("MINIMAL", "BASIC", "FULL"):
        try:
            t = cli.get_task(tid, view=view)
            d = t.as_dict()
            has_logs = bool(d.get("logs"))
            ok = (view == "MINIMAL" and set(d) <= {"id", "state"}) or (
                view != "MINIMAL" and d.get("executors")
            )
            rep.step(
                f"GetTask view={view} parses as py-tes type",
                bool(ok),
                keys=sorted(d),
                has_logs=has_logs,
            )
        except Exception as exc:
            rep.step(f"GetTask view={view}", False, error=repr(exc))

    # Wait until done (py-tes wait polls)
    try:
        done = cli.wait(tid, timeout=120)
        rep.step(
            "wait -> terminal state",
            done.state == "COMPLETE",
            state=done.state,
            stdout=excerpt([log.stdout for lg in (done.logs or []) for log in (lg.logs or [])]),
        )
    except Exception as exc:
        rep.step("wait -> terminal state", False, error=repr(exc))

    # List and paging (page_size=1 -> next_page_token -> next page). Create a second task
    # so there are at least 2
    try:
        second_id = cli.create_task(
            tes.Task(
                name="interop-tes-1 sleep",
                executors=[tes.Executor(image="alpine:3.20", command=["sleep", "120"])],
                tags={"interop": "tes-1"},
            )
        )
        page1 = cli.list_tasks(view="MINIMAL", page_size=1)
        d1 = page1.as_dict()
        tok = d1.get("next_page_token")
        page2 = cli.list_tasks(view="MINIMAL", page_size=1, page_token=tok) if tok else None
        d2 = page2.as_dict() if page2 else {}
        ids1 = [t["id"] for t in d1.get("tasks", [])]
        ids2 = [t["id"] for t in d2.get("tasks", [])]
        rep.step(
            "ListTasks page_size=1 and next page via page_token",
            bool(tok) and len(ids1) == 1 and len(ids2) == 1 and ids1 != ids2,
            page1=ids1,
            page2=ids2,
            has_token=bool(tok),
        )
        full = cli.list_tasks(view="FULL", page_size=5).as_dict()
        rep.step(
            "ListTasks view=FULL parses as py-tes type",
            all("executors" in t for t in full.get("tasks", [])),
            n=len(full.get("tasks", [])),
        )
    except Exception as exc:
        rep.step("ListTasks and paging", False, error=repr(exc))
        return rep.save("INTEROP-TES-1.json")

    # Cancel -> CANCELED (may pass through CANCELING)
    try:
        cli.cancel_task(second_id)
        state = None
        for _ in range(60):
            state = cli.get_task(second_id, view="MINIMAL").state
            if state in ("CANCELED", "COMPLETE", "EXECUTOR_ERROR", "SYSTEM_ERROR"):
                break
            time.sleep(1)
        rep.step("CancelTask → CANCELED", state == "CANCELED", state=state)
    except Exception as exc:
        rep.step("CancelTask → CANCELED", False, error=repr(exc))
    return rep.save("INTEROP-TES-1.json")


if __name__ == "__main__":
    raise SystemExit(main())
