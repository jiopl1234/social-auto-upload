import asyncio
from pathlib import Path

import pytest
from patchright.async_api import async_playwright

from orchestrator.errors import TaskError
from uploader.tencent_uploader.main import TencentBaseUploader


def test_realtime_verification_stops_without_saving_qr_or_waiting_for_scan(tmp_path: Path):
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, channel="chrome")
            try:
                page = await browser.new_page()
                await page.set_content('<div class="weui-desktop-dialog__wrp"><div>需用管理员微信扫码完成实名验证后才能继续发表</div><div>QR</div></div>')
                uploader = TencentBaseUploader(publish_date=0, account_file="unused")
                qr_path = tmp_path / "verification.png"
                with pytest.raises(TaskError) as error:
                    await uploader.wait_for_realtime_verification(page, qr_path=qr_path)
                assert error.value.code == "AUTH_REQUIRED"
                assert not qr_path.exists()
                assert await page.locator("div.weui-desktop-dialog__wrp").count() == 1
            finally:
                await browser.close()
    asyncio.run(scenario())
