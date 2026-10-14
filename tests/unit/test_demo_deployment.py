from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_demo_service_and_installer_keep_privileges_separate():
    service = (ROOT / "deploy/systemd/pa-agent-demo-worker.service").read_text()
    assert "User=pa-agent-execution" in service
    assert "x-simulated" not in service  # Header belongs to immutable client, not configuration.
    assert "LoadCredential=okx-demo-secret:" in service
    assert "ProtectSystem=strict" in service
    assert "ReadOnlyPaths=/var/lib/pa-agent-execution/intents" in service
    assert "/var/lib/pa-agent/records" not in service
    backup = (ROOT / "deploy/systemd/pa-agent-demo-backup.service").read_text()
    assert "User=pa-agent-execution" in backup
    assert "LoadCredential" not in backup
    assert "ReadWritePaths=/var/lib/pa-agent-execution/state /var/backups/pa-agent-demo" in backup
    installer = (ROOT / "deploy/install_vps.sh").read_text()
    assert "pa-agent-demo-worker.service; do" in installer
    assert "-m 2750" in installer and "pa-agent-execution-spool" in installer
    assert "pa-agent-execution/state" in installer and "-m 0700" in installer
    assert "systemctl start pa-agent-demo" not in installer


def test_shared_atomic_files_apply_group_read_before_rename():
    source = (ROOT / "pa_agent/execution/demo_spool.py").read_text()
    assert source.index("os.fchmod") < source.index("os.replace")
