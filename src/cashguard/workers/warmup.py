"""Explicit readiness boundary; model warmup belongs to model owners."""


def warmup(pipeline_mode: str) -> None:
    if pipeline_mode != "stub":
        raise RuntimeError(
            "Доступен только synthetic stub; runner и model adapters ожидаются от S0/S3/S6"
        )
