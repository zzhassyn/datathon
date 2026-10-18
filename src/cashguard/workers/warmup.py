"""Explicit readiness boundary; the Windows worker retains its verified PT models."""


def warmup(pipeline_mode: str, settings=None):
    if pipeline_mode == "full" and settings is not None and settings.vision_backend == "torch":
        import os

        import torch  # pyright: ignore[reportMissingImports]  # Separate ML worker interpreter.

        from cashguard.core.atomic import atomic_write_json
        from cashguard.pipeline.local_config import model_fingerprint, read_config
        from cashguard.pipeline.perception.windows_inference import (
            deny_network,
            gpu_lock,
            load_models,
        )

        deny_network()
        torch.set_num_threads(1)
        config = read_config(settings.local_analysis_config)
        device = (
            "cuda:0"
            if torch.cuda.is_available() and config.get("device", "auto") != "cpu"
            else "cpu"
        )
        with gpu_lock(config["gpu_lock_path"], device != "cpu"):
            bundle = load_models(config, device)
        atomic_write_json(
            settings.var_dir / "local-model-ready.json",
            {
                "worker_pid": os.getpid(),
                "models": bundle[1],
                "models_hash": model_fingerprint(settings.local_analysis_config),
            },
        )
        return bundle
    if pipeline_mode != "stub":
        raise RuntimeError(
            "Доступен только synthetic stub; runner и model adapters ожидаются от S0/S3/S6"
        )
