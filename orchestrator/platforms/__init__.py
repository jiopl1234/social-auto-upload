"""Explicit platform capabilities; production evidence is not inferred from tests."""
from __future__ import annotations

from pathlib import Path



def capability_report() -> list[dict]:
    report = [
        {"platform": platform, "content_type": kind, "status": "restricted",
         "offline_verified": True, "offline_evidence": "synthetic_dom_and_task_protocol",
         "production_verified": False, "capability_version": "1.0.0",
         "missing_evidence": ["stable_account_id", "per_asset_processing", "work_detail_readback"]}
        for platform, kinds in (("douyin", ("video", "note")),
                                ("xiaohongshu", ("video", "note")), ("tencent", ("video",)))
        for kind in kinds
    ]
    report[0].update({
        "status": "available_limited", "can_execute": True, "capability_version": "1.1.0",
        "live_evidence_verified": True, "production_verified": False,
        "missing_evidence": ["cli_end_to_end_live_submission"],
        "live_evidence": "docs/audit/douyin-live-video-2026-09-27.md",
        "supported_scope": "original_non_ai_video_public_now_no_tags_collection_custom_cover_or_declarations",
        "recovery": "receipt_id_and_uploaded_video_id_required",
    })
    return report


def default_adapters(runtime_dir: Path, *, headed: bool = False, prepare_only: bool = False) -> dict:
    from orchestrator.browser import BrowserSession
    from orchestrator.platforms.base import BrowserDeliveryAdapter, PreparationOnlyAdapter
    from orchestrator.platforms.profiles import create_port

    adapters = {platform: BrowserDeliveryAdapter(runtime_dir, lambda p=platform: create_port(p),
        session_factory=lambda context: BrowserSession(context, launch_options={"headless": not headed, "channel": "chromium"}))
        for platform in ("douyin", "xiaohongshu", "tencent")}
    return {key: PreparationOnlyAdapter(value) if prepare_only else value for key, value in adapters.items()}
