"""Exercise install.sh piped into sh, as curl would, with fake git and ollama."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FAKE_GIT = f"""#!/bin/sh
echo "git $*" >> "$TEST_LOG"
if [ "$1" = clone ]; then
    for dest; do :; done
    mkdir -p "$dest/.git"
    cp "{ROOT}"/*.py "{ROOT}"/*.json "$dest"
fi
"""

FAKE_OLLAMA = """#!/bin/sh
case "$1" in
    --version) echo "ollama version is ${TEST_OLLAMA_VERSION:-0.40.4}" ;;
    list) [ -z "${TEST_OLLAMA_DOWN:-}" ] ;;
    pull) echo "ollama $*" >> "$TEST_LOG" ;;
esac
"""

FAKE_UNAME = """#!/bin/sh
echo "${TEST_UNAME:-Linux}"
"""

class InstallTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="qsh-install-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.log = self.root / "log"
        self.log.touch()
        self.install_dir = self.root / "home" / "qsh"
        self.bin_dir = self.root / "bin"
        stubs = self.root / "stubs"
        stubs.mkdir()
        for name, script in (("git", FAKE_GIT), ("ollama", FAKE_OLLAMA), ("uname", FAKE_UNAME)):
            (stubs / name).write_text(script, encoding="utf-8")
            (stubs / name).chmod(0o755)
        self.env = os.environ.copy()
        self.env.update({
            "PATH": f"{stubs}{os.pathsep}{self.env['PATH']}",
            "TEST_LOG": str(self.log),
            "QSH_DIR": str(self.install_dir),
            "QSH_BIN_DIR": str(self.bin_dir),
        })

    def install(self, **env):
        return subprocess.run(
            ["sh"], input=(ROOT / "install.sh").read_text(), capture_output=True, text=True,
            cwd=self.root, env={**self.env, **env}, timeout=30, check=False,
        )

    def assert_nothing_written(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.install_dir.exists())
        self.assertFalse((self.bin_dir / "qsh").exists())
        self.assertNotIn("ollama pull", self.log.read_text())

    def test_installs_link_and_models(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        link = self.bin_dir / "qsh"
        self.assertEqual(Path(os.readlink(link)), self.install_dir / "qsh.py")
        self.assertTrue(os.access(link, os.X_OK))
        log = self.log.read_text()
        self.assertIn("ollama pull qwen2.5:3b-instruct", log)
        self.assertIn("ollama pull kev:0.8b-q8_0", log)
        self.assertIn("is not on your PATH", result.stdout)

    def test_reinstall_updates_existing_checkout(self):
        self.assertEqual(self.install().returncode, 0)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pull --ff-only", self.log.read_text())

    def test_macos_installs_original_models(self):
        result = self.install(TEST_UNAME="Darwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log.read_text()
        self.assertIn("ollama pull qwen2.5-coder:1.5b-instruct-q4_K_S", log)
        self.assertIn("ollama pull kev:0.8b-mxfp8", log)

    def test_old_ollama_fails_before_writing(self):
        result = self.install(TEST_OLLAMA_VERSION="0.40.3")
        self.assertIn("Ollama 0.40.4 or newer is required (found 0.40.3)", result.stderr)
        self.assert_nothing_written(result)

    def test_unreachable_ollama_fails_before_writing(self):
        result = self.install(TEST_OLLAMA_DOWN="1")
        self.assertIn("cannot reach the Ollama server", result.stderr)
        self.assert_nothing_written(result)

    def test_unwritable_bin_dir_fails_before_writing(self):
        locked = self.root / "locked"
        locked.mkdir()
        locked.chmod(0o555)
        self.addCleanup(locked.chmod, 0o755)
        result = self.install(QSH_BIN_DIR=str(locked / "bin"))
        self.assertIn(f"cannot write to {locked}", result.stderr)
        self.assertFalse(self.install_dir.exists())
        self.assertNotIn("ollama pull", self.log.read_text())

    def test_existing_foreign_command_is_not_replaced(self):
        self.bin_dir.mkdir()
        (self.bin_dir / "qsh").write_text("other")
        result = self.install()
        self.assertIn("already exists and is not a qsh link", result.stderr)
        self.assertEqual((self.bin_dir / "qsh").read_text(), "other")
        self.assertFalse(self.install_dir.exists())

    def test_existing_unrelated_directory_is_not_reused(self):
        self.install_dir.mkdir(parents=True)
        result = self.install()
        self.assertIn("is not a qsh checkout", result.stderr)
        self.assertFalse((self.bin_dir / "qsh").exists())


if __name__ == "__main__":
    unittest.main()
