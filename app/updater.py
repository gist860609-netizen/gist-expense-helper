"""GitHub Releases 기반 자동 업데이트."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from version import VERSION, REPO, TAG_PREFIX, ASSET


def _ver(s: str) -> tuple:
    return tuple(int(x) for x in s.lstrip("v").split(".") if x.isdigit())


def check() -> dict:
    """같은 저장소에 프로그램이 여럿이라 태그 접두어와 파일 이름으로 내 릴리스만 고른다."""
    try:
        r = requests.get(f"https://api.github.com/repos/{REPO}/releases?per_page=30", timeout=8)
        if r.status_code != 200:
            return {"available": False, "current": VERSION}
        rels = [x for x in r.json() if x.get("tag_name", "").startswith(TAG_PREFIX)
                and x["tag_name"][len(TAG_PREFIX):][:1].isdigit()]
        if not rels:
            return {"available": False, "current": VERSION}
        rel = max(rels, key=lambda x: _ver(x["tag_name"][len(TAG_PREFIX):]))
        latest = rel["tag_name"][len(TAG_PREFIX):]
        asset = next((a for a in rel.get("assets", []) if a["name"] == ASSET), None)
        return {
            "available": bool(asset) and _ver(latest) > _ver(VERSION),
            "current": VERSION,
            "latest": latest,
            "notes": rel.get("body", "")[:400],
            "url": asset["browser_download_url"] if asset else "",
        }
    except Exception:
        return {"available": False, "current": VERSION}


def apply(url: str) -> None:
    """새 exe를 내려받고, 프로그램 종료 후 교체·재실행하는 배치를 띄운다."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("개발 실행 중에는 업데이트할 수 없습니다.")
    exe = Path(sys.executable)
    tmp = Path(tempfile.gettempdir()) / f"{exe.stem}_new.exe"
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    bat = Path(tempfile.gettempdir()) / "geh_update.bat"
    bat.write_text(
        "@echo off\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f':retry\r\nmove /y "{tmp}" "{exe}" >nul 2>&1 || (timeout /t 1 >nul & goto retry)\r\n'
        f'start "" "{exe}"\r\n'
        'del "%~f0"\r\n',
        encoding="cp949",
    )
    # 새 exe가 이전 프로세스의 임시폴더(_MEI…)를 이어받지 않도록 환경을 초기화해서 실행
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("_MEI") or k.startswith("_PYI") or k == "TCL_LIBRARY" or k == "TK_LIBRARY")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=0x08000000, env=env)
    os._exit(0)
