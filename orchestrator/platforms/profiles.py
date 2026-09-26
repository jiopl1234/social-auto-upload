"""Candidate editor selectors recovered from the legacy uploaders.

These locate controls only. No stable-identity, processed-asset or work-detail
mapping has been verified against a live account, so default ports stay gated.
"""
from orchestrator.platforms.dom import DomPagePort, EditorSelectors, PageEvidence


EDITORS = {
    "douyin": EditorSelectors(
        upload="input.upload-btn-input", title='input[placeholder="填写作品标题"]',
        body='div.zone-container[contenteditable="true"]', submit='button:has-text("发布")',
    ),
    "xiaohongshu": EditorSelectors(
        upload="div[class^='upload-content'] input.upload-input", title='input[placeholder*="填写标题"]',
        body='p[data-placeholder*="输入正文描述"]', submit='button:has-text("发布")',
    ),
    "tencent": EditorSelectors(
        upload='input[type="file"][accept*="video"]', title='input[placeholder="填写短标题有机会获得更多流量"]',
        body="div.input-editor", submit='button:has-text("发表")',
    ),
}
EDIT_URLS = {
    "douyin": "https://creator.douyin.com/creator-micro/content/upload",
    "xiaohongshu": "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video",
    "tencent": "https://channels.weixin.qq.com/platform/post/create",
}
WORK_URLS = {
    "douyin": "https://creator.douyin.com/creator-micro/content/manage",
    "xiaohongshu": "https://creator.xiaohongshu.com/new/note-manager",
    "tencent": "https://channels.weixin.qq.com/platform/post/list",
}


def create_port(platform: str, *, evidence: PageEvidence | None = None,
                selectors: EditorSelectors | None = None, edit_url: str | None = None,
                works_url: str | None = None) -> DomPagePort:
    return DomPagePort(platform, selectors or EDITORS[platform],
                       edit_url=edit_url or EDIT_URLS[platform], works_url=works_url or WORK_URLS[platform],
                       evidence=evidence)
