"""Explicit platform capabilities; production evidence is not inferred from tests."""
from __future__ import annotations

from pathlib import Path
from typing import Callable



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
    report[2].update({
        "status": "available_limited", "can_prepare": True, "can_execute": True, "capability_version": "1.2.1",
        "live_preparation_verified": True, "production_verified": False,
        "live_evidence_verified": True, "live_submission_verified": True, "live_recovery_verified": True,
        "live_receipt_verified": True, "live_receipt_recovery_verified": True,
        "live_evidence": "docs/audit/xiaohongshu-live-video-2026-10-01.md",
        "missing_evidence": ["live_inline_receipt_persistence"],
        "supported_scope": "original_non_ai_video_public_now_up_to_32_mib_no_tags_collection_custom_cover_or_declarations",
        "recovery": "original_video_md5_required_without_receipt_complete_unique_listing_required",
    })
    report[4].update({
        "status": "available_limited", "can_prepare": True, "can_execute": True, "capability_version": "1.2.0",
        "live_preparation_verified": True, "live_submission_verified": True, "live_recovery_verified": True,
        "live_evidence_verified": True, "live_receipt_verified": True,
        "production_verified": True, "live_cli_submission_verified": True,
        "live_receipt_kind": "positive_acknowledgement_without_content_id",
        "live_evidence": "docs/audit/channels-cli-video-2026-10-01.md",
        "missing_evidence": [],
        "supported_scope": "original_non_ai_video_public_now_up_to_32_mib_single_paragraph_short_title_6_to_16_utf16_no_tags_location_link_activity_collection_custom_cover_or_declarations",
        "account_evidence": "authenticated_creator_list_and_independent_detail",
        "result_scope": "accepted_work_not_moderation_or_distribution",
        "recovery": "positive_creation_ack_and_source_md5_complete_creator_listing_required_without_server_id",
    })
    return report


def default_adapters(runtime_dir: Path, *, headed: bool = False, prepare_only: bool = False,
                     verification_wait: float = 0, on_verification: Callable[[str], None] | None = None) -> dict:
    from orchestrator.browser import BrowserSession
    from orchestrator.platforms.base import BrowserDeliveryAdapter, PreparationOnlyAdapter
    from orchestrator.platforms.profiles import create_port
    from orchestrator.platforms.xiaohongshu import XiaohongshuVideoPreparationPort
    from orchestrator.platforms.channels import ChannelsVideoPreparationPort

    def port_for(platform):
        if prepare_only and platform == "xiaohongshu":
            return XiaohongshuVideoPreparationPort()
        if prepare_only and platform == "tencent":
            return ChannelsVideoPreparationPort()
        return create_port(platform)

    adapters = {platform: BrowserDeliveryAdapter(runtime_dir,
        lambda p=platform: port_for(p),
        verification_wait=verification_wait if platform in {"douyin", "xiaohongshu"} else 0, on_verification=on_verification,
        session_factory=lambda context: BrowserSession(context, launch_options={"headless": not headed, "channel": "chromium"}))
        for platform in ("douyin", "xiaohongshu", "tencent")}
    return {key: PreparationOnlyAdapter(value) if prepare_only else value for key, value in adapters.items()}
