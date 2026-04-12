"""
Deploy signal bot to VPS via SSH/SFTP.
Run from the repo root:
    python deploy/hetzner/deploy_signal_bot.py
"""
from __future__ import annotations

import sys
sys.stdout.reconfigure(encoding="utf-8")
import os
import tarfile
import tempfile
from pathlib import Path

try:
    import paramiko
except ImportError:
    print("Install paramiko first:  pip install paramiko")
    sys.exit(1)

VPS_HOST = "103.6.170.25"
VPS_PORT = 22
VPS_USER = "root"
VPS_PASS = "Priceoffreedom$1"
REMOTE_DIR = "/opt/telegram-trader-signal-bot"
SERVICE_NAME = "telegram-trader-signal-bot"

LOCAL_ROOT = Path(__file__).resolve().parent.parent.parent  # repo root


def _ssh() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(VPS_HOST, port=VPS_PORT, username=VPS_USER, password=VPS_PASS, timeout=30)
    return client


def run(client: paramiko.SSHClient, cmd: str) -> str:
    _, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    if out:
        print(f"  -> {out}")
    if err:
        print(f"  ! {err}")
    return out


def upload_src(client: paramiko.SSHClient) -> None:
    EXCLUDES = {".venv", "__pycache__", "*.pyc", ".git", "tests", "logs", "data"}
    src_root = LOCAL_ROOT / "src"

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name

    with tarfile.open(tmp_path, "w:gz") as tar:
        for item in src_root.rglob("*"):
            relative = item.relative_to(src_root)
            parts = set(relative.parts)
            if parts & EXCLUDES:
                continue
            if item.suffix == ".pyc":
                continue
            if item.is_file():
                tar.add(item, arcname=str(relative))

    sftp = client.open_sftp()
    remote_tar = "/tmp/signal_bot_src.tar.gz"
    print(f"Uploading src archive ({Path(tmp_path).stat().st_size // 1024}KB)...")
    sftp.put(tmp_path, remote_tar)
    sftp.close()
    os.unlink(tmp_path)

    run(client, f"mkdir -p {REMOTE_DIR}/src")
    run(client, f"tar -xzf {remote_tar} -C {REMOTE_DIR}/src --overwrite")
    run(client, f"rm {remote_tar}")
    print("src/ extracted.")


def upload_pyproject(client: paramiko.SSHClient) -> None:
    for fname in ("pyproject.toml", "requirements.txt"):
        local = LOCAL_ROOT / fname
        if local.exists():
            sftp = client.open_sftp()
            sftp.put(str(local), f"{REMOTE_DIR}/{fname}")
            sftp.close()
            print(f"{fname} uploaded.")


def main() -> None:
    print(f"Connecting to {VPS_HOST}...")
    client = _ssh()
    print("Connected.")

    run(client, f"mkdir -p {REMOTE_DIR}/data {REMOTE_DIR}/logs")

    upload_src(client)
    upload_pyproject(client)

    # Re-install package in existing venv
    print("Installing updated package...")
    run(client, f"{REMOTE_DIR}/.venv/bin/pip install --quiet -e {REMOTE_DIR}")

    # Restart service
    run(client, f"systemctl restart {SERVICE_NAME}")
    import time; time.sleep(3)
    status = run(client, f"systemctl status {SERVICE_NAME} --no-pager -l")
    print("\n--- Service status ---")
    print(status)

    client.close()
    print("\nSignal bot deploy complete.")


if __name__ == "__main__":
    main()
