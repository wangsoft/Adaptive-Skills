from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_release_contract as release_contract


def main() -> int:
    root = ROOT
    release_contract.main()

    tauri = json.loads(
        (root / "app" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    updater = tauri["plugins"]["updater"]
    if updater.get("dangerousInsecureTransportProtocol"):
        raise RuntimeError("The updater must not allow insecure transport")

    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8").split("\0")
    private_key_candidates = [
        path
        for path in tracked
        if path and (path.endswith(".key") or "private-key" in path.casefold())
    ]
    if private_key_candidates:
        raise RuntimeError(
            f"Updater private-key candidates are tracked: {private_key_candidates}"
        )

    update_source = (root / "app" / "src" / "update.ts").read_text(encoding="utf-8")
    if "performUpdateCheck" not in update_source or "installConfirmedUpdate" not in update_source:
        raise RuntimeError("Update checking and confirmed installation are not separated")
    if "supportsInAppUpdates" not in update_source:
        raise RuntimeError("Updater bundle-type safety policy is missing")

    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    if "secrets.TAURI_SIGNING_PRIVATE_KEY" not in workflow:
        raise RuntimeError("The release build does not receive the updater signing secret")
    verifier = (root / "scripts" / "verify_desktop_bundle.py").read_text(encoding="utf-8")
    if '"latest.json"' not in verifier or '"signature"' not in verifier:
        raise RuntimeError("The verified release assembler does not produce the update feed")

    print(f"updater contract ok: v{release_contract.RELEASE_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
