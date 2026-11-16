"""지출 건 도우미 — 진입점 (pywebview 창 + Python 백엔드)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import webview

import core
import extractor
import updater
from version import VERSION

ALLOWED = (".pdf", ".xlsx", ".xlsm", ".jpg", ".jpeg", ".png", ".hwp", ".hwpx", ".docx")


def resource(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", Path(__file__).parent)
    return str(Path(base) / rel)


class Api:
    def __init__(self):
        self.cfg = core.load_config()
        self.window = None

    # ----- 상태·설정 -----
    def state(self):
        s = self.cfg.get("settings_cache") or {}
        return {
            "version": VERSION,
            "root": self.cfg["root"],
            "connected": bool(self.cfg["script_url"] and self.cfg["token"]),
            "script_url": self.cfg["script_url"],
            "token_masked": (self.cfg["token"][:4] + "••••" + self.cfg["token"][-2:]) if self.cfg["token"] else "",
            "settings": s,
        }

    def choose_root(self):
        res = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if not res:
            return {"ok": False}
        self.cfg["root"] = res[0]
        core.save_config(self.cfg)
        made = 0
        if self.cfg.get("settings_cache"):
            made = core.ensure_structure(self.cfg["root"], self.cfg["settings_cache"])
        return {"ok": True, "root": self.cfg["root"], "made": made}

    def connect(self, url: str, token: str):
        url, token = url.strip(), token.strip()
        if url:
            self.cfg["script_url"] = url
        if token and "••" not in token:
            self.cfg["token"] = token
        core.save_config(self.cfg)
        return self.refresh_settings()

    def refresh_settings(self):
        try:
            s = self._sheet().settings()
            self.cfg["settings_cache"] = s
            core.save_config(self.cfg)
            made = core.ensure_structure(self.cfg["root"], s) if self.cfg["root"] else 0
            return {"ok": True, "settings": s, "made": made}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _sheet(self):
        return core.Sheet(self.cfg["script_url"], self.cfg["token"])

    # ----- 파일 인식 -----
    def pick_files(self):
        res = self.window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=("증빙 파일 (*.pdf;*.xlsx;*.jpg;*.png;*.hwp;*.hwpx)", "모든 파일 (*.*)"))
        return self.extract_paths(list(res or []))

    def extract_paths(self, paths):
        out = []
        for p in paths:
            if Path(p).suffix.lower() in ALLOWED and Path(p).is_file():
                out.append(extractor.extract(p, self.cfg.get("vendors", {})))
        return out

    # ----- 저장 -----
    def save(self, form: dict):
        try:
            if not self.cfg["root"]:
                return {"ok": False, "error": "먼저 상위 폴더를 지정하세요."}
            s = self.cfg.get("settings_cache") or {}
            sm = next((x for x in s.get("semoks", []) if x["semok"] == form["semok"]), {})
            d = {**form, "budget_item": sm.get("budget_item", ""), "bimok": sm.get("bimok", "")}
            d["amount"] = int(str(d.get("amount", 0)).replace(",", "") or 0)
            res = core.save_case(self.cfg["root"], d, form.get("files", []))
            title = core.approval_title(d)
            sheet_msg = ""
            if self.cfg["script_url"]:
                docs = ", ".join(sorted({f.get("doc_type", "") for f in form.get("files", []) if f.get("doc_type")}))
                memo = f"{docs}({d['vendor']})" if d.get("vendor") else docs
                if form.get("memo"):
                    memo += f" / {form['memo']}"
                row = self._sheet().append({
                    "task": d["task"], "date": d["date"], "semok": d["semok"],
                    "usage": core.usage_text(d), "amount": d["amount"], "pay": d.get("pay", ""),
                    "memo": memo, "zeus": d.get("zeus") or "견적서", "ezbaro": "미결의",
                    "progress": "ZEUS 구매신청",
                })
                sheet_msg = f"시트 {row['row']}행 등록"
            if d.get("biz_no") and d.get("vendor"):
                self.cfg.setdefault("vendors", {})[d["biz_no"]] = d["vendor"]
                core.save_config(self.cfg)
            return {"ok": True, **res, "title": title, "sheet": sheet_msg}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ----- 대시보드 -----
    def pending(self):
        try:
            items = self._sheet().pending()
            for it in items:
                it["next"] = core.next_step(it.get("zeus", ""), it.get("ezbaro", ""))
            return {"ok": True, "items": items}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def update_stage(self, it: dict):
        try:
            self._sheet().update(it["task"], int(it["row"]), it.get("zeus", ""), it.get("ezbaro", ""), it.get("progress", ""))
            renamed = ""
            if self.cfg["root"]:
                iso = str(it.get("date", ""))[:10].replace(".", "-")
                kw = it.get("vendor") or ""
                if not kw:
                    import re
                    m = re.search(r"\(([^()]+)\)\s*$", it.get("usage", ""))
                    kw = m.group(1) if m else ""
                f = core.find_case_folder(self.cfg["root"], it["task"], iso, kw)
                if f:
                    renamed = core.apply_tag(f, it.get("zeus", ""), it.get("ezbaro", ""), it.get("progress", "")).name
            return {"ok": True, "renamed": renamed, "next": core.next_step(it.get("zeus", ""), it.get("ezbaro", ""))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ----- 기타 -----
    def copy_text(self, text: str):
        subprocess.run("clip", input=text.encode("utf-16le"), shell=True)
        return True

    def open_folder(self, path: str):
        if os.name == "nt":
            os.startfile(path)
        return True

    def check_update(self):
        return updater.check()

    def do_update(self, url: str):
        try:
            updater.apply(url)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def on_loaded(window, api):
    try:
        from webview.dom import DOMEventHandler

        def on_drop(e):
            files = e.get("dataTransfer", {}).get("files", [])
            paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
            if paths:
                import json
                res = api.extract_paths(paths)
                window.evaluate_js(f"window.onFilesExtracted({json.dumps(res, ensure_ascii=False)})")

        window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
    except Exception:
        pass


def main():
    api = Api()
    window = webview.create_window(
        f"지출 건 도우미 {VERSION}", resource("ui/index.html"), js_api=api,
        width=1180, height=820, min_size=(980, 680))
    api.window = window
    window.events.loaded += lambda: on_loaded(window, api)
    # 창을 닫을 때 진행 중인 요청(시트·업데이트 확인)이나 내부 이벤트 처리 때문에
    # '응답하지 않음'이 뜨지 않도록, 설정은 이미 저장돼 있으므로 즉시 종료한다
    window.events.closing += lambda: os._exit(0)
    webview.start()


if __name__ == "__main__":
    main()
