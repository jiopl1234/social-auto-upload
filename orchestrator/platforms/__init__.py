"""Explicit platform capabilities; production evidence is not inferred from tests."""
from __future__ import annotations

from pathlib import Path



def capability_report() -> list[dict]:
    return [
        {"platform": platform, "content_type": kind, "status": "restricted",
         "production_verified": False, "capability_version": "1.0.0",
         "missing_evidence": ["stable_account_id", "per_asset_processing", "work_detail_readback"]}
        for platform, kinds in (("douyin", ("video", "note")),
                                ("xiaohongshu", ("video", "note")), ("tencent", ("video",)))
        for kind in kinds
    ]


def default_adapters(runtime_dir: Path) -> dict:
    from orchestrator.platforms.base import BrowserDeliveryAdapter
    from orchestrator.platforms.profiles import create_port

    return {platform: BrowserDeliveryAdapter(runtime_dir, lambda p=platform: create_port(p))
            for platform in ("douyin", "xiaohongshu", "tencent")}
