import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliStartupTests(unittest.TestCase):
    """Exercise the CLI in an initialized checkout without installed adapters."""

    def test_help_does_not_require_unused_platform_dependencies(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            shutil.copy2(project / "sau_cli.py", checkout / "sau_cli.py")
            shutil.copy2(project / "conf.example.py", checkout / "conf.py")
            shutil.copytree(project / "orchestrator", checkout / "orchestrator", ignore=shutil.ignore_patterns("__pycache__"))
            result = subprocess.run(
                [sys.executable, "-X", "utf8", "-S", str(checkout / "sau_cli.py"), "--help"],
                cwd=checkout,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("douyin", result.stdout)
        self.assertIn("xiaohongshu", result.stdout)
        self.assertIn("tencent", result.stdout)

    def test_all_platform_help_and_missing_accounts_need_no_browser(self):
        project = Path(__file__).resolve().parents[1]
        platforms = (
            "douyin", "xiaohongshu", "tencent", "bilibili", "kuaishou",
            "baijiahao", "alipay", "weibo", "hupu", "youtube",
        )
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            shutil.copy2(project / "sau_cli.py", checkout / "sau_cli.py")
            shutil.copy2(project / "conf.example.py", checkout / "conf.py")
            shutil.copytree(project / "orchestrator", checkout / "orchestrator", ignore=shutil.ignore_patterns("__pycache__"))
            for platform in platforms:
                for arguments, expected_code in ((["--help"], 0), (["check", "--account", "missing"], 1)):
                    with self.subTest(platform=platform, arguments=arguments):
                        result = subprocess.run(
                            [sys.executable, "-X", "utf8", "-S", str(checkout / "sau_cli.py"), platform, *arguments],
                            cwd=checkout,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            stdin=subprocess.DEVNULL,
                            timeout=15,
                        )
                        self.assertEqual(result.returncode, expected_code, result.stderr)
                        if expected_code:
                            self.assertEqual(result.stdout.strip(), "invalid")
                            self.assertEqual(result.stderr, "")
