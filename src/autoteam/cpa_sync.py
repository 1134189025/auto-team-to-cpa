"""CPA sync helpers for child auth files."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

import autoteam.config as config

logger = logging.getLogger(__name__)


def _headers():
    return {"Authorization": f"Bearer {config.CPA_KEY.strip()}"}


def list_cpa_files():
    cpa_url = config.CPA_URL.strip()
    resp = requests.get(f"{cpa_url}/v0/management/auth-files", headers=_headers(), timeout=10)
    if resp.status_code != 200:
        logger.error("[CPA] 获取文件列表失败: %d", resp.status_code)
        return []
    data = resp.json()
    return data.get("files", [])


def upload_to_cpa(filepath):
    filepath = Path(filepath)
    if not filepath.exists():
        logger.warning("[CPA] 文件不存在: %s", filepath)
        return False

    cpa_url = config.CPA_URL.strip()
    with open(filepath, "rb") as file_obj:
        resp = requests.post(
            f"{cpa_url}/v0/management/auth-files",
            headers=_headers(),
            files={"file": (filepath.name, file_obj, "application/json")},
            timeout=10,
        )

    if resp.status_code == 200:
        logger.info("[CPA] 已上传: %s", filepath.name)
        return True

    logger.error("[CPA] 上传失败: %d %s", resp.status_code, resp.text[:200])
    return False


def delete_from_cpa(name):
    cpa_url = config.CPA_URL.strip()
    resp = requests.delete(
        f"{cpa_url}/v0/management/auth-files",
        headers=_headers(),
        params={"name": name},
        timeout=10,
    )
    if resp.status_code == 200:
        logger.info("[CPA] 已删除: %s", name)
        return True
    logger.error("[CPA] 删除失败: %d %s", resp.status_code, resp.text[:200])
    return False


def sync_main_codex_to_cpa(auth_file):
    """Compatibility wrapper for the legacy single-main sync flow."""
    return sync_parent_main_auth_to_cpa("main", auth_file)


def sync_parent_main_auth_to_cpa(parent_id, auth_file):
    auth_path = Path(auth_file).expanduser().resolve()
    if not auth_path.exists():
        raise FileNotFoundError(f"主号 auth 文件不存在: {auth_path}")

    existing = next(
        (
            item
            for item in list_cpa_files()
            if str(item.get("name") or item.get("filename") or item.get("file_name") or "").strip() == auth_path.name
        ),
        None,
    )
    deleted_existing = False
    if existing:
        deleted_existing = delete_from_cpa(auth_path.name)
        if not deleted_existing:
            raise RuntimeError(f"CPA 删除旧文件失败: {auth_path.name}")

    if not upload_to_cpa(str(auth_path)):
        raise RuntimeError(f"CPA 上传失败: {auth_path.name}")

    logger.info("[CPA] 母号主号 auth 已同步: parent=%s file=%s", parent_id, auth_path.name)
    return {
        "parent_id": str(parent_id or "").strip(),
        "auth_file": str(auth_path),
        "filename": auth_path.name,
        "deleted_existing": deleted_existing,
        "uploaded": True,
    }


def resync_ready_accounts():
    from autoteam.accounts import (
        STATUS_AUTH_SAVED,
        STATUS_BLOCKED,
        STATUS_READY,
        STATUS_REMOVED,
        discover_auth_file,
        load_accounts,
        update_account_by_id,
    )

    uploaded = 0
    skipped = 0
    failed = 0
    repaired = 0
    now = time.time()

    for account in load_accounts():
        auth_file = discover_auth_file(account.get("email", ""), account.get("auth_file", ""))
        updates = {}
        if auth_file and auth_file != account.get("auth_file"):
            updates["auth_file"] = auth_file
        if account.get("status") in {STATUS_BLOCKED, STATUS_REMOVED}:
            if updates:
                update_account_by_id(account["id"], **updates)
                repaired += 1
            skipped += 1
            continue
        if auth_file and account.get("status") not in {STATUS_AUTH_SAVED, STATUS_READY}:
            updates["status"] = STATUS_READY if account.get("cpa_uploaded_at") else STATUS_AUTH_SAVED
            if not account.get("auth_saved_at"):
                updates["auth_saved_at"] = now
        if updates:
            account = update_account_by_id(account["id"], **updates) or {**account, **updates}
            repaired += 1

        if account.get("status") not in {STATUS_AUTH_SAVED, STATUS_READY}:
            skipped += 1
            continue
        if not auth_file or not Path(auth_file).exists():
            failed += 1
            update_account_by_id(
                account["id"],
                status=STATUS_AUTH_SAVED,
                error="CPA upload failed: missing auth file",
                error_stage="cpa",
                completed_at=None,
            )
            continue
        if upload_to_cpa(auth_file):
            uploaded += 1
            update_account_by_id(
                account["id"],
                auth_file=auth_file,
                status=STATUS_READY,
                auth_saved_at=account.get("auth_saved_at") or now,
                cpa_uploaded_at=time.time(),
                error="",
                error_stage="",
                completed_at=time.time(),
            )
        else:
            failed += 1
            update_account_by_id(
                account["id"],
                auth_file=auth_file,
                status=STATUS_AUTH_SAVED,
                auth_saved_at=account.get("auth_saved_at") or now,
                error="CPA upload failed",
                error_stage="cpa",
                completed_at=None,
            )

    summary = {"uploaded": uploaded, "failed": failed, "skipped": skipped, "repaired": repaired}
    logger.info("[CPA] 重传完成: 上传 %d, 修复 %d, 失败 %d, 跳过 %d", uploaded, repaired, failed, skipped)
    return summary
