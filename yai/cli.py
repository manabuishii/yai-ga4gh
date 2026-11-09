"""yai serve <aai|drs|tes|wes> --config <file> --port <n>

The API starts with --workers 1 and no auto reload (tes.md section 2, wes.md section 4).
Listens on 127.0.0.1 only by default (D-002).
"""

from __future__ import annotations

import argparse
import importlib
import logging

import uvicorn

from yai.common import startup
from yai.common.config import config_path, load_config
from yai.common.logsetup import setup_logging

SERVICES = ("aai", "drs", "tes", "wes")
DEFAULT_PORTS = {"aai": 8401, "drs": 8402, "tes": 8403, "wes": 8404}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="yai")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("service", choices=SERVICES)
    serve.add_argument("--config")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int)
    drs = sub.add_parser("drs")
    drs_sub = drs.add_subparsers(dest="drs_command", required=True)
    reg = drs_sub.add_parser("register", help="register a file as a DRS object")
    reg.add_argument("file")
    reg.add_argument("--config")
    reg.add_argument("--policy", default="public", choices=["public", "bearer", "dataset"])
    reg.add_argument("--metadata-policy", choices=["public", "bearer", "dataset"])
    reg.add_argument("--access-policy", choices=["public", "bearer", "dataset"])
    reg.add_argument("--dataset", help="dataset policy URL (compared with the Visa value)")
    reg.add_argument("--name")
    reg.add_argument("--mime-type")
    reg.add_argument("--created-time", help="content creation time (RFC 3339); default: mtime")
    reg.add_argument("--description")
    setds = drs_sub.add_parser("set-dataset", help="change an object's dataset assignment")
    setds.add_argument("object_id")
    setds.add_argument("dataset")
    setds.add_argument("--config")
    tes = sub.add_parser("tes")
    tes_sub = tes.add_subparsers(dest="tes_command", required=True)
    worker = tes_sub.add_parser("worker", help="TES worker (only one runs)")
    worker.add_argument("--config")
    args = parser.parse_args(argv)

    path = config_path(args.config)
    config = load_config(path)
    if args.command == "drs":
        from yai.drs.app import register_main, set_dataset_main

        if args.drs_command == "set-dataset":
            set_dataset_main(args, config)
        else:
            register_main(args, config)
        return
    if args.command == "tes":
        from yai.tes.worker import main as worker_main

        worker_main(config, path)
        return
    setup_logging(logging.WARNING)
    module = importlib.import_module(f"yai.{args.service}.app")
    app = module.create_app(config)
    startup.log_start(args.service, config, path)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port or DEFAULT_PORTS[args.service],
        workers=1,
        reload=False,
        # access logs are off so that query parts are not kept (system.md section 8)
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
