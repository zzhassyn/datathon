"""Local service entrypoints. No ML modules are loaded by the API command."""

import argparse
import hashlib
import json
import multiprocessing
from pathlib import Path

from cashguard.core.env import apply_offline_env
from cashguard.core.logging import configure_logging
from cashguard.core.settings import get_settings


def main() -> None:
    multiprocessing.set_start_method("spawn", force=True)
    apply_offline_env()
    parser = argparse.ArgumentParser(
        description="CashGuard: локальный synthetic stub backend"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    up = commands.add_parser("up", help="API и worker под supervisor")
    up.add_argument("--profile", choices=["dev", "prod"], default="dev")
    up.add_argument("--port", type=int)
    api = commands.add_parser("api", help="Только API-процесс")
    api.add_argument("--reload", action="store_true")
    commands.add_parser("status", help="Состояние воркеров без видео и кадров")
    args = parser.parse_args()
    settings = get_settings()
    settings.paths.ensure()
    configure_logging(settings.paths.logs_dir / f"{args.command}.jsonl")
    if args.command == "up":
        if args.port is not None:
            settings.port = args.port
        elif args.profile == "dev" and settings.port == 8000:
            digest = hashlib.sha256(str(Path.cwd().resolve()).encode()).digest()
            settings.port = 8100 + int.from_bytes(digest[:4], "big") % 100
        from cashguard.workers.supervisor import Supervisor

        Supervisor(settings, profile=args.profile).run()
    elif args.command == "api":
        import uvicorn

        uvicorn.run(
            "cashguard.api.main:create_app",
            factory=True,
            host=settings.host,
            port=settings.port,
            reload=args.reload,
            reload_dirs=[str(Path(__file__).parent)] if args.reload else None,
            workers=1,
            timeout_keep_alive=100,
            timeout_graceful_shutdown=30,
            limit_concurrency=64,
            proxy_headers=False,
            forwarded_allow_ips="127.0.0.1",
        )
    else:
        from cashguard.jobs.repo import JobRepository

        workers = JobRepository(settings.db_path).workers()
        print(
            json.dumps(
                {"pipeline_mode": settings.pipeline_mode, "workers": workers},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
