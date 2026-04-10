"""
Deploy scalper bot to VPS via SSH/SFTP.
Run from your local Windows machine:
    python scalper/deploy/deploy_scalper.py
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

# ── VPS settings ────────────────────────────────────────────────────────────
VPS_HOST = "103.6.170.25"
VPS_PORT = 22
VPS_USER = "root"
VPS_PASS = "Priceoffreedom$1"
REMOTE_DIR = "/opt/scalper"
SERVICE_NAME = "scalper"
# ────────────────────────────────────────────────────────────────────────────

LOCAL_ROOT = Path(__file__).resolve().parent.parent  # .../scalper/


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
        print(f"  → {out}")
    if err:
        print(f"  ! {err}")
    return out


def upload_tarball(client: paramiko.SSHClient) -> None:
    EXCLUDES = {".venv", "__pycache__", "*.pyc", "data", "deploy", ".git"}

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name

    with tarfile.open(tmp_path, "w:gz") as tar:
        for item in LOCAL_ROOT.rglob("*"):
            # Skip excluded paths
            relative = item.relative_to(LOCAL_ROOT)
            parts = set(relative.parts)
            if parts & EXCLUDES:
                continue
            if item.suffix in {".pyc"}:
                continue
            if item.is_file():
                tar.add(item, arcname=str(relative))

    sftp = client.open_sftp()
    remote_tar = "/tmp/scalper_deploy.tar.gz"
    print(f"Uploading archive ({Path(tmp_path).stat().st_size // 1024}KB)…")
    sftp.put(tmp_path, remote_tar)
    sftp.close()
    os.unlink(tmp_path)
    return remote_tar


def upload_env(client: paramiko.SSHClient) -> None:
    env_path = LOCAL_ROOT / ".env"
    if not env_path.exists():
        print("WARNING: No .env found — skipping env upload")
        return
    sftp = client.open_sftp()
    sftp.put(str(env_path), f"{REMOTE_DIR}/.env")
    run(client, f"chmod 600 {REMOTE_DIR}/.env")
    sftp.close()
    print(".env uploaded")


def upload_service(client: paramiko.SSHClient) -> None:
    svc_path = LOCAL_ROOT / "deploy" / f"{SERVICE_NAME}.service"
    if not svc_path.exists():
        print(f"WARNING: {svc_path} not found — skipping service install")
        return
    sftp = client.open_sftp()
    sftp.put(str(svc_path), f"/etc/systemd/system/{SERVICE_NAME}.service")
    sftp.close()
    print("Service file installed")


def main() -> None:
    print(f"Connecting to {VPS_HOST}…")
    client = _ssh()
    print("Connected.")

    # Ensure directories exist
    run(client, f"mkdir -p {REMOTE_DIR}/data {REMOTE_DIR}/logs")

    # Upload and extract
    remote_tar = upload_tarball(client)
    run(client, f"tar -xzf {remote_tar} -C {REMOTE_DIR} --overwrite")
    run(client, f"rm {remote_tar}")
    print("Files extracted.")

    # Set up venv and install
    print("Setting up virtual environment…")
    run(client, f"python3 -m venv {REMOTE_DIR}/.venv")
    run(client, f"{REMOTE_DIR}/.venv/bin/pip install --quiet --upgrade pip")
    run(client, f"{REMOTE_DIR}/.venv/bin/pip install --quiet requests")
    print("Dependencies installed.")

    # Upload .env
    upload_env(client)

    # Install and enable service
    upload_service(client)
    run(client, "systemctl daemon-reload")
    run(client, f"systemctl enable {SERVICE_NAME}")
    run(client, f"systemctl restart {SERVICE_NAME}")
    import time; time.sleep(3)
    status = run(client, f"systemctl status {SERVICE_NAME} --no-pager -l")
    print("\n--- Service status ---")
    print(status)

    client.close()
    print("\nDeploy complete.")


if __name__ == "__main__":
    main()
