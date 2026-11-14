"""설정 저장, 폴더·파일명 규칙, 구글시트(Apps Script) 연동."""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import date
from pathlib import Path

import requests

APP_NAME = "GistExpenseHelper"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "root": "",
    "script_url": "",
    "token": "",
    "vendors": {},          # 사업자번호 -> 업체명 (저장할 때마다 자동 학습)
    "settings_cache": {},   # 시트 설정 탭(과제·세목) 캐시
}

# 단계 → 폴더명 꼬리표
TAG_DONE = "(완)"
TAG_PROGRESS = "(진행중)"
BAD_CHARS = re.compile(r'[\\/:*?"<>|]')


# ---------- 설정 ----------
def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return {**DEFAULT_CONFIG, **data}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- 이름 규칙 ----------
def safe(s: str) -> str:
    return re.sub(r"\s+", " ", BAD_CHARS.sub("", s or "")).strip()


def yymmdd(iso: str) -> str:
    try:
        y, m, d = iso[:10].split("-")
        return f"{y[2:]}{m}{d}"
    except Exception:
        return date.today().strftime("%y%m%d")


def task_no(task: str) -> str:
    """'1-2 권인찬' -> '1-2'"""
    return task.split(" ")[0] if task else ""


def bimok_folder(bimok: str) -> str:
    """'4. 연구재료비' -> '4_연구재료비'"""
    m = re.match(r"\s*(\d+)\s*[.)]?\s*(.+)", bimok or "")
    return safe(f"{m.group(1)}_{m.group(2)}") if m else safe(bimok or "기타")


def case_folder_name(d: dict) -> str:
    amt = f"{int(d.get('amount') or 0):,}원"
    if d.get("item"):
        core = f"{d.get('vendor', '')}_{d['item']}"
    else:
        core = d.get("vendor") or d.get("semok", "")
    return safe(f"{yymmdd(d['date'])}_{core}({amt})")


def file_name(d: dict, ext: str) -> str:
    core = d.get("vendor", "")
    if d.get("item"):
        core += f"_{d['item']}"
    return safe(f"({d.get('doc_type', '증빙자료')}) [{task_no(d['task'])}] "
                f"{d.get('budget_item', '')}-{core}({yymmdd(d['date'])})") + ext


def approval_title(d: dict) -> str:
    """「[과제] 세목(YYMMDD)_핵심내용」, 핵심내용 20자 안팎"""
    core = f"{d.get('vendor', '')} {d.get('item', '')}".strip()
    if len(core) > 22:
        cut = core[:22]
        core = cut[:cut.rfind(" ")] if " " in cut[8:] else cut.rstrip()
    return f"[{task_no(d['task'])}] {d.get('budget_item') or d.get('semok', '')}({yymmdd(d['date'])})_{core}"


def usage_text(d: dict) -> str:
    """시트 '집행 내역(사용 명목)' 칸"""
    if d.get("item") and d.get("vendor"):
        return f"{d['item']} 구매({d['vendor']})"
    return d.get("memo") or d.get("item") or d.get("vendor") or ""


# ---------- 폴더 ----------
def ensure_structure(root: str, settings: dict) -> int:
    """과제 / 비목 폴더를 한 번에 만든다. 만든 폴더 수 반환."""
    made = 0
    bimoks = sorted({s["bimok"] for s in settings.get("semoks", []) if s.get("bimok")})
    for t in settings.get("tasks", []):
        for b in bimoks:
            p = Path(root) / safe(t["name"]) / bimok_folder(b)
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                made += 1
    return made


def save_case(root: str, d: dict, files: list[dict]) -> dict:
    """건별 폴더를 만들고 파일을 규칙대로 복사한다."""
    base = Path(root) / safe(d["task"]) / bimok_folder(d.get("bimok", ""))
    folder = base / case_folder_name(d)
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        src = Path(f["file"])
        meta = {**d, "doc_type": f.get("doc_type") or d.get("doc_type")}
        target = folder / file_name(meta, src.suffix.lower())
        n = 2
        while target.exists():
            target = folder / f"{target.stem} ({n}){target.suffix}"
            n += 1
        shutil.copy2(src, target)
        saved.append(target.name)
    return {"folder": str(folder), "files": saved}


def find_case_folder(root: str, task: str, iso_date: str, keyword: str) -> Path | None:
    tdir = Path(root) / safe(task)
    if not tdir.exists():
        return None
    prefix = yymmdd(iso_date) if iso_date else ""
    kw = safe(keyword)[:6]
    for p in tdir.glob("*/*"):
        if p.is_dir() and (not prefix or p.name.startswith(prefix)) and (not kw or kw in p.name):
            return p
    return None


def apply_tag(folder: Path, zeus: str, ezbaro: str, progress: str) -> Path:
    name = folder.name.replace(TAG_DONE, "").replace(TAG_PROGRESS, "").strip()
    if ezbaro == "집행" or progress == "완료":
        name += TAG_DONE
    elif "결재 진행중" in (zeus or "") or "결재 진행중" in (progress or ""):
        name += TAG_PROGRESS
    target = folder.with_name(name)
    if target != folder and not target.exists():
        folder.rename(target)
        return target
    return folder


# ---------- 다음 단계 안내 ----------
NEXT_STEPS = {
    "미신청": "견적서 받기 → ZEUS 구매신청",
    "견적서": "내부결재 → ZEUS 주문서 등록",
    "주문서": "납품 후 거래명세서 · 검수사진 · 세금계산서 수령",
    "1차검수": "재무팀 검수 확인 → 지급신청",
    "검수 중": "검수 완료 대기",
    "증빙자료": "증빙서 합본 → 지급신청서 상신",
    "결재 진행중": "결재 완료 대기",
    "결재 완료": "이지바로 등록",
    "이지바로전송": "이지바로 결의 · 집행 확인",
    "회의록 작성": "회의록 · 증빙서(영수증) 작성 → 상신",
}


def next_step(zeus: str, ezbaro: str) -> str:
    if ezbaro == "집행":
        return "완료"
    if zeus == "이지바로전송" and ezbaro == "미결의":
        return "이지바로 결의 진행"
    return NEXT_STEPS.get(zeus or "", "")


# ---------- 시트 연동 (Apps Script 웹앱) ----------
class Sheet:
    def __init__(self, url: str, token: str):
        self.url, self.token = url, token

    def _post(self, payload: dict) -> dict:
        if not self.url:
            raise RuntimeError("시트 연결 주소가 없습니다. 설정에서 입력하세요.")
        r = requests.post(self.url, json={**payload, "token": self.token}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("error") or "시트 처리 실패")
        return data

    def settings(self) -> dict:
        return self._post({"action": "settings"})["data"]

    def append(self, d: dict) -> dict:
        return self._post({"action": "append", "row": d})["data"]

    def pending(self) -> list[dict]:
        return self._post({"action": "pending"})["data"]

    def update(self, task: str, row: int, zeus: str, ezbaro: str, progress: str) -> dict:
        return self._post({"action": "update", "task": task, "row": row,
                           "zeus": zeus, "ezbaro": ezbaro, "progress": progress})["data"]
