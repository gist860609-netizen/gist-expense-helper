"""마찌언니 블로그 자동 작성기 — 원고 폴더를 고르면 네이버에 직접 입력해 임시저장한다."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import webview

import updater
from naver import APP_DIR, Stop, Writer, load_selectors
from version import VERSION

CONFIG = APP_DIR / "config.json"


def resource(rel: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / rel


def load_cfg() -> dict:
    try:
        return {"blog_id": "jung901123", "speed": 1.0, "minimize": True,
                **json.loads(CONFIG.read_text(encoding="utf-8"))}
    except Exception:
        return {"blog_id": "jung901123", "speed": 1.0, "minimize": True, "last_folder": ""}


class Api:
    def __init__(self):
        self.cfg = load_cfg()
        self.window = None
        self.writer = None
        self.running = False

    def _save(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def _log(self, msg: str):
        if self.window:
            self.window.evaluate_js(f"window.addLog({json.dumps(msg, ensure_ascii=False)})")

    def state(self):
        return {**self.cfg, "version": VERSION, "running": self.running}

    def set_option(self, key, value):
        self.cfg[key] = value
        self._save()
        return True

    def choose_folder(self):
        res = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if not res:
            return {"ok": False}
        folder = Path(res[0])
        md = folder / "final.md" if (folder / "final.md").exists() else folder / "out" / "final.md"
        if not md.exists():
            return {"ok": False, "error": "이 폴더에 final.md가 없습니다."}
        from mdparse import parse
        doc = parse(md)
        counts = {}
        for b in doc["blocks"]:
            counts[b["type"]] = counts.get(b["type"], 0) + 1
        self.cfg["last_folder"] = str(folder)
        self._save()
        return {"ok": True, "folder": str(folder), "title": doc["title"], "counts": counts,
                "tags": doc["tags"]}

    def start(self):
        if self.running:
            return {"ok": False, "error": "이미 진행 중입니다."}
        folder = self.cfg.get("last_folder")
        if not folder:
            return {"ok": False, "error": "원고 폴더를 먼저 고르세요."}
        self.running = True
        threading.Thread(target=self._run, args=(folder,), daemon=True).start()
        return {"ok": True}

    def _run(self, folder):
        from playwright.sync_api import sync_playwright
        try:
            sel = load_selectors(resource("selectors.json"))
            self.writer = Writer(folder, self.cfg["blog_id"], sel, self._log,
                                 float(self.cfg.get("speed", 1.0)), bool(self.cfg.get("minimize", True)))
            self._log("시작합니다. 붙여넣기 없이 한 글자씩 입력합니다.")
            with sync_playwright() as pw:
                res = self.writer.run(pw)
            self._log(f"완료 · 성공 {len(res['ok'])}건 · 실패 {len(res['fail'])}건")
            self.window.evaluate_js(f"window.done({json.dumps(res, ensure_ascii=False)})")
        except Stop:
            self._log("중지했습니다.")
            self.window.evaluate_js("window.done(null)")
        except Exception as e:
            self._log(f"오류: {e}")
            self.window.evaluate_js("window.done(null)")
        finally:
            self.running = False

    def stop(self):
        if self.writer:
            self.writer.stop_flag = True
        return True

    def open_out(self):
        p = Path(self.cfg.get("last_folder", "")) / "out"
        if p.exists() and os.name == "nt":
            os.startfile(p)
        return True

    def check_update(self):
        return updater.check()

    def do_update(self, url):
        try:
            updater.apply(url)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def main():
    api = Api()
    api.window = webview.create_window(f"블로그 자동 작성기 {VERSION}", str(resource("ui/index.html")),
                                       js_api=api, width=760, height=760, min_size=(640, 640))
    def on_closing():
        if api.writer:
            api.writer.stop_flag = True
        os._exit(0)
    api.window.events.closing += on_closing
    webview.start()


if __name__ == "__main__":
    main()
