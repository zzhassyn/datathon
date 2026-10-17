import argparse
import multiprocessing

from cashguard.core.env import apply_offline_env
from cashguard.core.logging import configure_logging
from cashguard.core.settings import get_settings


def main() -> None:
    multiprocessing.set_start_method("spawn", force=True)
    apply_offline_env()
    parser = argparse.ArgumentParser(description="CashGuard background worker")
    parser.add_argument("--role", choices=["all", "perception", "vlm"], default="all")
    parser.add_argument("--name")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.role == "vlm":
        parser.exit(2, "VLM worker ожидается от S6; stub не использует VLM\n")
    from cashguard.workers.perception_worker import PerceptionWorker

    settings = get_settings()
    configure_logging(settings.paths.logs_dir / f"worker-{args.role}.jsonl")
    PerceptionWorker(settings, name=args.name, role=args.role).run(once=args.once)


if __name__ == "__main__":
    main()
