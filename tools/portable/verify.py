"""Verify extracted portable binaries through CLI and real terminal interaction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import tarfile
import uuid
import zipfile

from terminal_session import TerminalSession
from advanced_cli import verify_advanced_cli
from advanced_tui import verify_advanced_tui


def check_archive(archive):
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    expected, filename = archive.with_name(archive.name + ".sha256").read_text().strip().split(maxsplit=1)
    assert digest == expected and filename.strip(" *") == archive.name, "Archive checksum mismatch"
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
    else:
        with tarfile.open(archive) as bundle:
            members = bundle.getmembers()
            names = [member.name for member in members]
            executable = bundle.getmember("log-analyzer/log-analyzer")
            assert executable.mode & 0o111, "Linux archive lost executable permissions"
            for member in members:
                if member.issym() or member.islnk():
                    target = PurePosixPath(member.linkname)
                    assert not target.is_absolute() and ".." not in target.parts, "Unsafe archive link"
    for name in names:
        path = PurePosixPath(name)
        assert not path.is_absolute() and ".." not in path.parts
        assert path.parts[0] == "log-analyzer", f"Unexpected archive root: {name}"
    assert "log-analyzer/LICENSE" in names, "Application license missing from portable archive"
    expected_styles = [
        "tui.base.tcss",
        "tui.dialog.tcss",
        "tui.layout.tcss",
        "tui.responsive.advanced.tcss",
        "tui.responsive.form.tcss",
        "tui.responsive.layout.tcss",
        "tui.time-range.tcss",
    ]
    styles = [
        PurePosixPath(name).name
        for name in names
        if "/log_analyzer/tcss/" in name and name.endswith(".tcss")
    ]
    assert sorted(styles) == sorted(expected_styles), f"Unexpected application stylesheets: {styles}"
    return digest


class Target:
    def __init__(self, archive, root, runtime_image):
        self.archive, self.root = archive, root
        self.data = root / "data"
        self.runtime_image = runtime_image
        self.container = None
        self.environment = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            self.environment.pop(key, None)
        for directory in ("work A", "work B", "profile", "home"):
            (self.data / directory).mkdir(parents=True)
        if runtime_image:
            self._start_container()
        else:
            assert os.name == "nt" and archive.suffix == ".zip", "Use --runtime-image for Linux archives"
            extracted = root / "解壓 Portable Folder"
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(extracted)
            self.bundle_dir = extracted / "log-analyzer"
            self.executable = str(self.bundle_dir / "log-analyzer.exe")
            self.environment["APPDATA"] = str(self.data / "profile")
            self.environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
            assert shutil.which("python", path=self.environment["PATH"]) is None
            assert shutil.which("python3", path=self.environment["PATH"]) is None
            assert shutil.which("uv", path=self.environment["PATH"]) is None
            self.initial_bundle_files = {p.relative_to(self.bundle_dir) for p in self.bundle_dir.rglob("*")}

    def _start_container(self):
        self.docker = shutil.which("docker")
        assert self.docker, "Docker is required for the clean Linux runtime"
        self.container = "log-analyzer-verify-" + uuid.uuid4().hex[:12]
        command = [
            self.docker, "run", "--detach", "--rm", "--name", self.container,
            "--user", "1000:1000", "--workdir", "/work",
            "--env", "HOME=/work/home", "--env", "XDG_CONFIG_HOME=/work/profile",
            "--env", "LANG=C.UTF-8", "--env", "TERM=xterm-256color",
            "--mount", f"type=bind,source={self.archive.parent},target=/input,readonly",
            "--mount", f"type=bind,source={self.data},target=/work",
            self.runtime_image, "sleep", "infinity",
        ]
        subprocess.run(command, check=True, capture_output=True, timeout=60)
        try:
            quoted = shlex.quote("/input/" + self.archive.name)
            setup = (
                "if command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1 || command -v uv >/dev/null 2>&1; "
                "then exit 91; fi\n"
                "mkdir /tmp/portable\n"
                f"tar -xzf {quoted} -C /tmp/portable\n"
                "chmod -R a-w /tmp/portable/log-analyzer\n"
                "test ! -w /tmp/portable/log-analyzer\n"
                "cat /etc/os-release\nldd --version"
            )
            self.executable = "/tmp/portable/log-analyzer/log-analyzer"
            result = self.run(["sh", "-ec", setup])
            (self.root / "runtime.txt").write_text(result.stdout, encoding="utf-8")
        except BaseException:
            self.close()
            raise

    def path(self, relative):
        return "/work/" + relative.replace("\\", "/") if self.container else str(self.data / relative)

    def command(self, args, *, cwd="work A", terminal=False):
        if not self.container:
            return list(args)
        return [self.docker, "exec", *(["-it"] if terminal else []),
                "--workdir", self.path(cwd), self.container, *args]

    def run(self, args, *, cwd="work A", expect_success=True):
        result = subprocess.run(
            self.command(args, cwd=cwd), cwd=self.data / cwd, env=self.environment,
            text=True, encoding="utf-8", capture_output=True, timeout=90,
        )
        if expect_success:
            assert result.returncode == 0, f"Command failed: {args}\n{result.stdout}\n{result.stderr}"
        assert "Traceback" not in result.stderr
        return result

    def terminal(self, evidence, *, cwd="work A", dimensions=(45, 160)):
        return TerminalSession(
            self.command([self.executable], cwd=cwd, terminal=True),
            self.data / cwd, self.environment, evidence, dimensions=dimensions,
        )

    def resize(self, terminal, columns, rows):
        terminal.resize(columns, rows)
        if self.container:
            self.run(["stty", "-F", "/dev/pts/0", "cols", str(columns), "rows", str(rows)])
        terminal.wait(
            lambda: (point := terminal.find("palette")) is not None
            and point[1] == rows - 1
            and terminal.find("Java Log Analyzer") is not None
            and terminal.find("執行結果") is not None
            and terminal.find("開始分析") is not None
            and terminal.find("匯出結果") is not None,
            f"Terminal did not render {columns}x{rows}",
        )

    def assert_bundle_untouched(self):
        if not self.container:
            assert {p.relative_to(self.bundle_dir) for p in self.bundle_dir.rglob("*")} == self.initial_bundle_files
        for directory in ("work A", "work B"):
            assert not (self.data / directory / ".java-log-analyzer.tui-state.json").exists()

    def close(self):
        if self.container:
            subprocess.run([self.docker, "stop", self.container], check=True, capture_output=True, timeout=30)
            self.container = None


def write_fixture(target):
    logs = target.data / "work A" / "logs 中文"
    logs.mkdir()
    (logs / "sample.log").write_text(
        "2026-10-24 10:00:00.000 [worker-A] ERROR Service - portable-alpha\n"
        "2026-10-24 11:00:00.000 [worker-B] ERROR Service - portable-alpha\n"
        "2026-10-24 11:01:00.000 [main] WARN Service - portable-beta\n", encoding="utf-8",
    )
    (logs / "sibling.log").write_text(
        "2026-10-24 11:02:00.000 [main] ERROR Service - excluded-sibling\n", encoding="utf-8",
    )
    return target.path("work A/logs 中文/sample.log")


def assert_analysis(terminal):
    def complete():
        text = "".join(terminal.text().split())
        return "狀態：完成" in text and "總Log3" in text and "命中3" in text
    terminal.wait(complete, "TUI analysis did not finish with exactly three matching entries")


def verify_first_session(target, input_path, root, name):
    with target.terminal(root / "tui-first") as terminal:
        terminal.wait_text("開始分析")
        terminal.fill_source_input(input_path)
        terminal.wait_text("單一檔案")
        terminal.click("管理分析配方")
        terminal.wait_text("輸入配方名稱")
        terminal.paste(name)
        terminal.click("另存新配方")
        terminal.wait_text("已另存新配方")
        terminal.capture("recipe-saved")
        terminal.click("關閉")
        terminal.wait(lambda: terminal.find("另存新配方") is None, "Recipe dialog did not close")
        terminal.click("開始分析")
        assert_analysis(terminal)
        terminal.capture("analysis-wide")
        target.resize(terminal, 80, 24)
        terminal.wait_text("開始分析")
        terminal.wait_text("匯出結果")
        terminal.capture("analysis-narrow")
        target.resize(terminal, 160, 45)
        terminal.click("匯出結果")
        terminal.wait_focused_button("執行匯出")
        terminal.send("\r")
        terminal.wait_text("匯出完成")
        terminal.capture("export-complete")
        terminal.send("q")
        terminal.wait_exit()
def verify_saved_files(target, input_path, name):
    reports = [path for path in (target.data / "work A").glob("*.csv") if path.name != "cli-report.csv"]
    assert reports, "TUI did not create a CSV report"
    content = "\n".join(path.read_text(encoding="utf-8-sig") for path in reports)
    assert "portable-alpha" in content and "portable-beta" in content
    assert "excluded-sibling" not in content
    profile = target.data / "profile" / "java-log-analyzer"
    recipes = (profile / "recipes.json").read_text(encoding="utf-8")
    assert name in recipes
    recent = json.loads((profile / "recent-state.json").read_text(encoding="utf-8"))
    assert recent["path"] == input_path

def verify_restarted_session(target, root, name):
    with target.terminal(root / "tui-restart", cwd="work B") as terminal:
        terminal.wait_text("單一檔案")
        terminal.fill_source_input(target.path("work B"))
        terminal.wait_text("沒有支援的日誌檔案")
        terminal.click("管理分析配方")
        terminal.wait_text("輸入配方名稱")
        terminal.click("選擇配方")
        terminal.wait_text(name)
        terminal.click(name)
        terminal.wait(lambda: terminal.find("選擇配方") is None, "Recipe selection did not complete")
        terminal.click("套用")
        terminal.wait(lambda: terminal.find("另存新配方") is None, "Recipe did not apply")
        terminal.wait_text("已載入配方")
        terminal.wait_text("單一檔案")
        terminal.click("開始分析")
        assert_analysis(terminal)
        terminal.capture("recipe-reloaded-analysis")
        terminal.send("q")
        terminal.wait_exit()


def verify_tui(target, input_path, root):
    name = "portable-smoke"
    verify_first_session(target, input_path, root, name)
    verify_saved_files(target, input_path, name)
    print("TUI analysis/export and persisted recipe verified; checking restart/load.", flush=True)
    verify_restarted_session(target, root, name)
    target.assert_bundle_untouched()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--runtime-image", help="No-Python Linux runtime image, e.g. debian:bookworm-slim")
    args = parser.parse_args()
    archive, root = args.archive.resolve(), args.work_dir.resolve()
    if root.exists():
        parser.error("--work-dir must be new; existing evidence is never overwritten")
    root.mkdir(parents=True)
    digest = check_archive(archive)
    target = Target(archive, root, args.runtime_image)
    try:
        input_path = write_fixture(target)
        version = target.run([target.executable, "--version"]).stdout.strip()
        assert version == "log-analyzer " + args.expected_version, version
        cli = target.run([target.executable, input_path, "-f", "json", "-o", target.path("work A/cli-report.json")])
        (root / "cli.txt").write_text(cli.stdout, encoding="utf-8")
        payload = json.loads((target.data / "work A/cli-report.json").read_text(encoding="utf-8"))
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "portable-alpha" in serialized and "portable-beta" in serialized
        assert "excluded-sibling" not in serialized
        assert payload["summary"] == {"ERROR": 2, "WARN": 1}
        assert {group["message"]: group["count"] for group in payload["results"]} == {
            "portable-alpha": 2, "portable-beta": 1,
        }
        for fmt in ("csv", "md"):
            target.run([target.executable, input_path, "-f", fmt, "-o", target.path(f"work A/cli-report.{fmt}")])
            content = (target.data / f"work A/cli-report.{fmt}").read_text(encoding="utf-8-sig")
            assert "portable-alpha" in content and "portable-beta" in content
            assert "excluded-sibling" not in content
        verify_tui(target, input_path, root)
        advanced_checks = verify_advanced_cli(target)
        print("Advanced CLI checks passed.", flush=True)
        advanced_checks += verify_advanced_tui(target, root)
        target.assert_bundle_untouched()
        assert digest == check_archive(archive), "Archive changed during verification"
        report = {
            "archive": archive.name, "sha256": digest, "version": version,
            "runtime": args.runtime_image or "Windows native; Python removed from PATH",
            "checks": ["default-tui-launch", "cli-json-csv-markdown-export", "tui-analysis-export", "recipe-save-restart-load",
                       "recent-state-cross-cwd", "wide-narrow-terminal", "bundle-not-written", *advanced_checks],
        }
        (root / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        target.close()


if __name__ == "__main__":
    main()
