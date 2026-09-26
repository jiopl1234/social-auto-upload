import asyncio
from pathlib import Path

from patchright.async_api import async_playwright

from uploader.tencent_uploader.main import TencentBaseUploader


def test_wait_for_realtime_verification_saves_qr_and_resumes(tmp_path: Path):
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, channel="chrome")
            page = await browser.new_page()
            await page.set_content(
                """
                <div class="weui-desktop-dialog__wrp">
                  <div>为保障账号安全，需用管理员微信扫码完成实名验证后才能继续发表</div>
                  <div class="qr">QR</div>
                </div>
                """
            )
            uploader = TencentBaseUploader(publish_date=0, account_file="unused")
            qr_path = tmp_path / "verification.png"

            async def dismiss_dialog():
                # A user can scan the exported QR only after its image is saved.
                async with asyncio.timeout(2):
                    while not qr_path.is_file() or qr_path.stat().st_size == 0:
                        await asyncio.sleep(0.01)
                await page.locator("div.weui-desktop-dialog__wrp").evaluate("el => el.remove()")

            dismiss_task = asyncio.create_task(dismiss_dialog())
            try:
                result = await uploader.wait_for_realtime_verification(
                    page,
                    qr_path=qr_path,
                    timeout_seconds=2,
                    poll_interval_seconds=0.05,
                )
                await dismiss_task
            finally:
                dismiss_task.cancel()
                await asyncio.gather(dismiss_task, return_exceptions=True)
                await browser.close()

            assert result == qr_path
            assert qr_path.is_file()
            assert qr_path.stat().st_size > 0

    asyncio.run(scenario())
