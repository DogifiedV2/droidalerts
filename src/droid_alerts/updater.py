from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .config import data_dir, project_root
from .network import certifi_ssl_context

USER_AGENT = f"DroidAlerts/{__version__}"

# Never let an update touch the user's own files: settings, credentials,
# captures, logs, and local training data all live in these folders.
PROTECTED_TOP_DIRS = {"config", "data", "training_data", ".git", "__pycache__"}
MANAGED_RELEASE_DIRS = ("src", "assets", "templates", "tools", "release-notes")
REQUIRED_RELEASE_DIRS = ("src/droid_alerts", "assets", "templates")
MANAGED_ROOT_FILES = (
    "main.py",
    "requirements.txt",
    "requirements-build.txt",
    "README.md",
    "EXTRA.md",
    "Droid Alerts.spec",
    "Build EXE.bat",
    "Start Droid Alerts.bat",
    "version_info.txt",
)


@dataclass(frozen=True)
class UpdateInstallResult:
    updated_files: int
    external_restart: bool = False


def _updates_dir() -> Path:
    return data_dir() / "updates"


def preferred_update_url(release: dict[str, str]) -> str:
    if getattr(sys, "frozen", False):
        return release.get("package_zip_url") or release.get("zip_url") or ""
    return release.get("source_zip_url") or release.get("zip_url") or ""


def download_and_install_update(
    zip_url: str,
    tag: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> UpdateInstallResult:
    """Download a release zip and copy its files over the install.

    Source runs can safely overwrite Python files before restart. Frozen exe
    runs need a small helper script because Windows locks the running exe; in
    that case this stages the update and starts the helper before the GUI exits.
    """
    if not zip_url:
        raise RuntimeError("The GitHub release is missing a downloadable update zip")
    if not zip_url.lower().startswith("https://"):
        raise RuntimeError("Updates must be downloaded over HTTPS")

    def report(text: str) -> None:
        if progress is not None:
            progress(text)

    updates = _updates_dir()
    extract_dir = updates / f"extract_{tag}".replace("/", "_")
    zip_path = updates / f"{tag}.zip".replace("/", "_")
    updates.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(extract_dir, ignore_errors=True)

    report(f"Downloading {tag}...")
    request = urllib.request.Request(zip_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(
        request,
        timeout=60,
        context=certifi_ssl_context(),
    ) as response, zip_path.open("wb") as fh:
        shutil.copyfileobj(response, fh)

    report("Unpacking...")
    with zipfile.ZipFile(zip_path) as zf:
        _safe_extract(zf, extract_dir)

    # GitHub archives wrap everything in a single "<repo>-<tag>/" folder.
    roots = [path for path in extract_dir.iterdir() if path.is_dir()]
    source_root = roots[0] if len(roots) == 1 else extract_dir
    is_source_update = (source_root / "main.py").exists() and (source_root / "src").is_dir()
    is_package_update = (source_root / "Droid Alerts.exe").exists() and (source_root / "_internal").is_dir()
    if getattr(sys, "frozen", False):
        if not is_package_update:
            shutil.rmtree(extract_dir, ignore_errors=True)
            zip_path.unlink(missing_ok=True)
            raise RuntimeError("The GitHub release is missing DroidAlerts.zip")
        return _stage_frozen_update(source_root, extract_dir, zip_path, tag, report)
    if not is_source_update:
        shutil.rmtree(extract_dir, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
        raise RuntimeError("The downloaded update doesn't look like Droid Alerts source")

    report("Installing files...")
    target_root = project_root()
    updated = _copy_update_files(source_root, target_root)

    shutil.rmtree(extract_dir, ignore_errors=True)
    zip_path.unlink(missing_ok=True)
    report(f"Installed {updated} updated files")
    return UpdateInstallResult(updated_files=updated)


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    """Reject path traversal and implausibly large release archives."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    total_size = 0
    for member in archive.infolist():
        total_size += max(0, member.file_size)
        if total_size > 1_500_000_000:
            raise RuntimeError("The downloaded update is unexpectedly large")
        target = (destination / member.filename).resolve()
        if target != root and root not in target.parents:
            raise RuntimeError("The downloaded update contains an unsafe file path")
    archive.extractall(destination)


def _copy_update_files(source_root: Path, target_root: Path) -> int:
    """Stage and atomically replace only release-owned source paths."""
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    _validate_source_release(source_root)
    target_root.mkdir(parents=True, exist_ok=True)

    work_root = Path(tempfile.mkdtemp(prefix=".droid_alerts_update_", dir=target_root))
    staged_root = work_root / "staged"
    backup_root = work_root / "backup"
    installed_paths: list[Path] = []
    backed_up_paths: list[tuple[Path, Path]] = []
    installed_files = 0
    try:
        for name in MANAGED_RELEASE_DIRS:
            source = source_root / name
            if not source.is_dir():
                continue
            staged = staged_root / name
            shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__"))
            installed_files += sum(path.is_file() for path in staged.rglob("*"))
        for name in MANAGED_ROOT_FILES:
            source = source_root / name
            if not source.is_file():
                continue
            staged = staged_root / name
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged)
            installed_files += 1

        for name in (*MANAGED_RELEASE_DIRS, *MANAGED_ROOT_FILES):
            staged = staged_root / name
            if not staged.exists():
                continue
            live = target_root / name
            backup = backup_root / name
            if live.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(live), str(backup))
                backed_up_paths.append((live, backup))
            shutil.move(str(staged), str(live))
            installed_paths.append(live)
    except Exception as exc:
        for live in reversed(installed_paths):
            _remove_path(live)
        for live, backup in reversed(backed_up_paths):
            if live.exists():
                _remove_path(live)
            if backup.exists():
                live.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(backup), str(live))
        raise RuntimeError(f"Could not install source update; previous files restored: {exc}") from exc
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
    return installed_files


def _validate_source_release(source_root: Path) -> None:
    required = ("main.py", *REQUIRED_RELEASE_DIRS)
    for relative in required:
        path = source_root / relative
        expected = path.is_file() if relative == "main.py" else path.is_dir()
        if not expected:
            raise RuntimeError(f"The source update is missing required path: {relative}")
        resolved = path.resolve()
        if resolved != source_root and source_root not in resolved.parents:
            raise RuntimeError(f"The source update contains an unsafe managed path: {relative}")
    for name in (*MANAGED_RELEASE_DIRS, *MANAGED_ROOT_FILES):
        managed = source_root / name
        if not managed.exists():
            continue
        paths = (managed, *managed.rglob("*")) if managed.is_dir() else (managed,)
        for path in paths:
            resolved = path.resolve()
            if resolved != source_root and source_root not in resolved.parents:
                relative = path.relative_to(source_root)
                raise RuntimeError(
                    f"The source update contains an unsafe managed path: {relative}"
                )


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _stage_frozen_update(
    source_root: Path,
    extract_dir: Path,
    zip_path: Path,
    tag: str,
    report: Callable[[str], None],
) -> UpdateInstallResult:
    if sys.platform != "win32":
        raise RuntimeError("Automatic packaged updates are only supported on Windows")

    report("Preparing restart...")
    updates = _updates_dir()
    script_path = updates / f"install_{tag}.ps1".replace("/", "_")
    log_path = updates / f"install_{tag}.log".replace("/", "_")
    target_root = project_root()
    exe_path = Path(sys.executable).resolve()
    script_path.write_text(_frozen_update_script(), encoding="utf-8")

    # CREATE_NO_WINDOW, not DETACHED_PROCESS: powershell.exe is a console app
    # and silently fails to start with no console at all when the parent is a
    # windowless GUI exe. NO_WINDOW gives it a hidden console instead.
    creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    boot_log = updates / f"install_{tag}.boot.log".replace("/", "_")
    boot_handle = boot_log.open("w", encoding="utf-8")
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-TargetRoot",
            str(target_root),
            "-SourceRoot",
            str(source_root),
            "-ExtractDir",
            str(extract_dir),
            "-ZipPath",
            str(zip_path),
            "-ExePath",
            str(exe_path),
            "-WaitPid",
            str(os.getpid()),
            "-LogPath",
            str(log_path),
        ],
        cwd=str(target_root),
        creationflags=creationflags,
        stdout=boot_handle,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )
    boot_handle.close()
    return UpdateInstallResult(updated_files=0, external_restart=True)


def _frozen_update_script() -> str:
    """Windows PowerShell 5.1-compatible helper (no pwsh-only APIs like
    [IO.Path]::GetRelativePath). Waits until the app process is really gone
    (aborts untouched if it never exits), swaps _internal wholesale so files
    removed by the new version don't linger, copies the exe last, and rolls
    the old _internal back if anything fails mid-copy."""
    protected = ", ".join(f"'{item}'" for item in sorted(PROTECTED_TOP_DIRS))
    return rf"""param(
    [Parameter(Mandatory=$true)][string]$TargetRoot,
    [Parameter(Mandatory=$true)][string]$SourceRoot,
    [Parameter(Mandatory=$true)][string]$ExtractDir,
    [Parameter(Mandatory=$true)][string]$ZipPath,
    [Parameter(Mandatory=$true)][string]$ExePath,
    [Parameter(Mandatory=$true)][int]$WaitPid,
    [Parameter(Mandatory=$true)][string]$LogPath
)
$ErrorActionPreference = 'Stop'
function Log([string]$Message) {{
    $parent = Split-Path -Parent $LogPath
    if ($parent) {{ New-Item -ItemType Directory -Path $parent -Force | Out-Null }}
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("$(Get-Date -Format o) " + $Message)
}}
function Relaunch {{
    Start-Process -FilePath $ExePath -ArgumentList 'gui' -WorkingDirectory $TargetRoot
}}
try {{
    Log "waiting for Droid Alerts (pid $WaitPid) to exit"
    $deadline = (Get-Date).AddSeconds(120)
    while ($true) {{
        $running = Get-Process -Id $WaitPid -ErrorAction SilentlyContinue
        if (-not $running) {{ break }}
        if ((Get-Date) -gt $deadline) {{
            Log "app never exited; aborting with no changes"
            exit 1
        }}
        Start-Sleep -Milliseconds 250
    }}
    Start-Sleep -Milliseconds 500

    # Swap _internal wholesale: copying over the old folder would leave
    # files the new version removed, the classic broken-after-update cause.
    $newInternal = Join-Path $SourceRoot '_internal'
    $liveInternal = Join-Path $TargetRoot '_internal'
    $backupInternal = Join-Path $TargetRoot '_internal.old'
    if (Test-Path -LiteralPath $backupInternal) {{
        Remove-Item -LiteralPath $backupInternal -Recurse -Force
    }}
    $swapped = $false
    if ((Test-Path -LiteralPath $liveInternal) -and (Test-Path -LiteralPath $newInternal)) {{
        Move-Item -LiteralPath $liveInternal -Destination $backupInternal -Force
        $swapped = $true
    }}
    try {{
        $protected = @({protected})
        $sourceFull = (Resolve-Path -LiteralPath $SourceRoot).Path.TrimEnd('\') + '\'
        $files = @(Get-ChildItem -LiteralPath $SourceRoot -Recurse -File)
        # Copy the exe last so a mid-copy failure never leaves a new exe
        # pointing at a half-written _internal.
        $ordered = @($files | Where-Object {{ $_.Name -ne 'Droid Alerts.exe' }}) +
                   @($files | Where-Object {{ $_.Name -eq 'Droid Alerts.exe' }})
        foreach ($file in $ordered) {{
            $relative = $file.FullName.Substring($sourceFull.Length)
            $parts = $relative -split '[\\/]'
            if ($parts.Length -gt 0 -and $protected -contains $parts[0]) {{ continue }}
            if ($parts -contains '__pycache__') {{ continue }}
            $destination = Join-Path $TargetRoot $relative
            $destinationParent = Split-Path -Parent $destination
            if ($destinationParent) {{
                New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
            }}
            Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        }}
        if ($swapped -and (Test-Path -LiteralPath $backupInternal)) {{
            Remove-Item -LiteralPath $backupInternal -Recurse -Force -ErrorAction SilentlyContinue
        }}
        Log "copy complete"
    }} catch {{
        Log ("copy failed: " + $_.Exception.Message)
        if ($swapped -and (Test-Path -LiteralPath $backupInternal)) {{
            Remove-Item -LiteralPath $liveInternal -Recurse -Force -ErrorAction SilentlyContinue
            Move-Item -LiteralPath $backupInternal -Destination $liveInternal -Force
            Log "restored previous _internal"
        }}
        throw
    }}
    Remove-Item -LiteralPath $ExtractDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
    Relaunch
    Log "restart launched"
}} catch {{
    Log ("update failed: " + $_.Exception.Message)
    Relaunch
    Log "relaunched previous version"
}}
"""


def restart_program() -> None:
    """Relaunch the GUI from the (now updated) source and exit this process.

    os._exit skips interpreter teardown so lingering watcher/overlay threads
    can't block the exit; the new process is fully detached.
    """
    creationflags = 0
    if sys.platform == "win32":
        # NO_WINDOW rather than DETACHED: python.exe (source installs) is a
        # console app and can fail to start fully console-less.
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.extend([str(project_root() / "main.py"), "gui"])
    else:
        command.append("gui")
    subprocess.Popen(
        command,
        cwd=str(project_root()),
        creationflags=creationflags,
        close_fds=True,
    )
    os._exit(0)


def exit_for_external_update() -> None:
    """Exit so the staged updater helper can replace the locked executable."""
    os._exit(0)
