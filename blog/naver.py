"""네이버 스마트에디터 직접 입력 엔진 (Playwright).

원칙
- 붙여넣기 없이 한 글자씩 입력하고, 색·크기·굵게·형광펜은 툴바 버튼으로 적용한다.
- 사진·GIF·동영상은 툴바의 사진/동영상 버튼으로 올린다 (파일 선택 창은 내부 처리, 화면 점유 없음).
- 발행하지 않는다. 임시저장까지만.
- 마우스·키보드를 점유하지 않는다 (전용 크롬 창을 내부 명령으로 조작).
"""
from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime
from pathlib import Path

import requests

from mdparse import parse, resolve

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "MazziBlogUploader"
PROFILE_DIR = APP_DIR / "chrome-profile"
SEL_URL = "https://raw.githubusercontent.com/gist860609-netizen/gist-expense-helper/main/blog/selectors.json"


def load_selectors(bundled: Path) -> dict:
    sel = json.loads(bundled.read_text(encoding="utf-8"))
    try:
        r = requests.get(SEL_URL, timeout=6)
        if r.ok:
            sel.update(r.json())
    except Exception:
        pass
    return sel


class Stop(Exception):
    pass


class Writer:
    def __init__(self, folder: str, blog_id: str, sel: dict, log, speed: float = 1.0,
                 minimize: bool = True):
        self.folder = Path(folder)
        self.blog_id = blog_id
        self.S = sel
        self.log = log
        self.speed = speed
        self.minimize = minimize
        self.result = {"ok": [], "fail": []}
        self.style = {"size": None, "color": None, "bg": None, "bold": False}
        self.stop_flag = False
        self.refocus = False
        self.in_quote = False

    # ---------- 공통 ----------
    def pause(self, a=0.25, b=0.7):
        time.sleep(random.uniform(a, b) * self.speed)

    def q(self, key: str, **fmt):
        css = self.S[key].format(**fmt) if fmt else self.S[key]
        return self.ed.locator(css)

    def click(self, key: str, timeout=6000, **fmt) -> bool:
        loc = self.q(key, **fmt).first
        try:
            loc.wait_for(state="visible", timeout=timeout)
            loc.click()
            self.pause(0.2, 0.5)
            return True
        except Exception:
            return False

    def step(self, name: str, fn, *a):
        if self.stop_flag:
            raise Stop()
        for attempt in range(3):
            try:
                if fn(*a) is not False:
                    self.result["ok"].append(name)
                    return True
            except Stop:
                raise
            except Exception as e:
                self.log(f"  재시도 {attempt + 1}/3 · {name} · {e.__class__.__name__}")
            self.pause(0.6, 1.2)
        self.result["fail"].append(name)
        self.log(f"✗ 실패: {name}")
        self.shot(f"fail_{len(self.result['fail'])}")
        return False

    def shot(self, name: str):
        try:
            out = self.folder / "out"
            out.mkdir(exist_ok=True)
            self.page.screenshot(path=str(out / f"{name}.png"), full_page=False)
        except Exception:
            pass

    # ---------- 사람처럼 입력 ----------
    def type_human(self, text: str):
        kb = self.page.keyboard
        for ch in text:
            if self.stop_flag:
                raise Stop()
            kb.type(ch)
            d = random.uniform(0.045, 0.14)
            if ch in ".,!?~" or ch == " ":
                d += random.uniform(0.05, 0.25)
            if random.random() < 0.02:
                d += random.uniform(0.4, 1.1)
            time.sleep(d * self.speed)

    def enter(self, n=1):
        for _ in range(n):
            self.page.keyboard.press("Enter")
            time.sleep(random.uniform(0.12, 0.3) * self.speed)

    # ---------- 서식 (툴바) ----------
    def set_size(self, size: int):
        if self.style["size"] == size:
            return
        if self.click("font_size_button") and self.click("font_size_option", size=size):
            self.style["size"] = size
        else:
            self.log(f"  글자 크기 {size} 적용 실패")

    def _pick_color(self, btn: str, hexv: str | None) -> bool:
        """팔레트에서 색을 고른다. 없는 색은 가장 가까운 팔레트 색으로 대체 (입력칸에 커서를 뺏기지 않도록)."""
        if not self.click(btn):
            return False
        if hexv is None:
            return self.click("color_none_option", timeout=3000)
        for h in (hexv.lower(), hexv.upper()):
            if self.click("color_option", timeout=1200, hex=h):
                return True
        try:
            colors = self.ed.eval_on_selector_all(
                "[data-color]", "els => els.filter(e => e.offsetParent).map(e => e.dataset.color)")
        except Exception:
            colors = []
        rgb = lambda h: tuple(int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        cands = [c for c in colors if c and len(c.lstrip("#")) == 6]
        if cands:
            t = rgb(hexv)
            best = min(cands, key=lambda c: sum((a - b) ** 2 for a, b in zip(rgb(c), t)))
            if self.click("color_option", timeout=1500, hex=best):
                if best.lower() != hexv.lower():
                    self.log(f"  {hexv} → 팔레트의 가장 가까운 색 {best}로 대체")
                return True
        if self.click("color_more_button", timeout=2500):
            inp = self.q("color_hex_input").first
            try:
                inp.click()
                inp.press("Control+A")
                self.page.keyboard.type(hexv.lstrip("#"))
                ok = self.click("color_hex_apply", timeout=2500)
                self.refocus = True
                return ok
            except Exception:
                pass
        self.page.keyboard.press("Escape")
        return False

    def set_color(self, hexv: str):
        if self.style["color"] == hexv:
            return
        if self._pick_color("font_color_button", hexv):
            self.style["color"] = hexv
        else:
            self.log(f"  글자색 {hexv} 적용 실패")

    def set_bg(self, hexv: str | None):
        if self.style["bg"] == hexv:
            return
        if self._pick_color("bg_color_button", hexv):
            self.style["bg"] = hexv
        else:
            self.log(f"  형광펜 {hexv} 적용 실패")

    def set_bold(self, on: bool):
        if self.style["bold"] != on:
            if self.click("bold_button"):
                self.style["bold"] = on

    def apply(self, seg: dict):
        self.refocus = False
        self.set_size(seg["size"])
        self.set_color(seg["color"])
        self.set_bg(seg["bg"])
        self.set_bold(seg["bold"])
        if self.refocus and not self.in_quote:
            self.to_end()

    def write_paras(self, paras: list[dict]):
        for i, p in enumerate(paras):
            for seg in p["segments"]:
                self.apply(seg)
                self.type_human(seg["text"])
            if i < len(paras) - 1:
                self.set_bg(None)
                self.enter()
            self.pause(0.3, 0.9)

    # ---------- 커서 위치 ----------
    def to_end(self):
        loc = self.q("body_last_paragraph").last
        loc.click()
        self.page.keyboard.press("End")
        self.pause(0.15, 0.3)

    # ---------- 컴포넌트 ----------
    def upload_images(self, files: list[Path], side: bool):
        with self.page.expect_file_chooser(timeout=10000) as fc:
            if not self.click("image_button"):
                return False
        fc.value.set_files([str(f) for f in files])
        self.pause(1.0, 1.6)
        if len(files) > 1:
            self.click("image_side_option" if side else "image_single_option", timeout=4000)
        self.ed.wait_for_timeout(2500 + 1200 * len(files))
        return True

    def upload_video(self, f: Path, title: str):
        if not self.click("video_button"):
            return False
        with self.page.expect_file_chooser(timeout=15000) as fc:
            if not self.click("video_file_button", timeout=10000):
                return False
        fc.value.set_files(str(f))
        self.log("  동영상 업로드 중…")
        self.ed.wait_for_timeout(8000)
        if title:
            try:
                inp = self.q("video_title_input").first
                inp.wait_for(state="visible", timeout=60000)
                inp.click()
                inp.press("Control+A")
                self.type_human(title)
            except Exception:
                self.log("  동영상 제목 입력 실패")
        deadline = time.time() + 300
        while time.time() < deadline:
            if self.click("video_done_button", timeout=3000):
                self.ed.wait_for_timeout(3000)
                return True
            time.sleep(3)
        return False

    def add_map(self, query: str):
        if not self.click("map_button"):
            return False
        inp = self.q("map_search_input").first
        inp.wait_for(state="visible", timeout=8000)
        inp.click()
        self.type_human(query)
        self.page.keyboard.press("Enter")
        self.ed.wait_for_timeout(2500)
        if not self.click("map_first_result_add", timeout=6000):
            self.page.keyboard.press("Escape")
            return False
        return self.click("map_done_button", timeout=5000)

    def add_divider(self):
        return self.click("divider_button")

    def add_quote(self, paras):
        if not self.click("quote_button"):
            return False
        self.pause(0.4, 0.8)
        self.in_quote = True
        try:
            self.write_paras(paras)
            self.set_bg(None)
        finally:
            self.in_quote = False
        return True

    # ---------- 실행 ----------
    def run(self, playwright) -> dict:
        md = next((p for p in [self.folder / "final.md", self.folder / "out" / "final.md"] if p.exists()), None)
        if not md:
            raise RuntimeError("final.md를 찾지 못했습니다.")
        doc = parse(md)
        self.log(f"원고: {doc['title']} · 블록 {len(doc['blocks'])}개 · 태그 {len(doc['tags'])}개")

        missing = []
        for b in doc["blocks"]:
            for n in b.get("files", []) + ([b["file"]] if b.get("file") else []):
                if not resolve(self.folder, n, "gif" if b["type"] == "gif" else "image"):
                    missing.append(n)
        if missing:
            self.log("⚠ 없는 파일: " + ", ".join(missing))

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        ctx = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="chrome", headless=os.environ.get("MAZZI_HEADLESS") == "1",
            viewport={"width": 1280, "height": 900}, locale="ko-KR",
            args=["--disable-blink-features=AutomationControlled"])
        self.page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            self.page.goto(self.S["write_url"].format(blog_id=self.blog_id), wait_until="domcontentloaded")
            self.page.wait_for_timeout(3000)
            if self.page.locator(self.S["login_marker"]).count():
                self.log("🔑 네이버 로그인이 필요합니다. 열린 크롬 창에서 직접 로그인해 주세요. (최대 5분 대기)")
                self.page.wait_for_url("**/postwrite**", timeout=300000)
                self.page.wait_for_timeout(3000)
            frame = self.page.frame(name="mainFrame")
            self.ed = frame if frame else self.page
            self.ed.wait_for_selector(self.S["title"], timeout=30000)
            if self.minimize:
                try:
                    cdp = ctx.new_cdp_session(self.page)
                    wid = cdp.send("Browser.getWindowForTarget")["windowId"]
                    cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})
                    self.log("크롬 창을 최소화했습니다. 다른 작업을 하셔도 됩니다.")
                except Exception:
                    pass
            self.click("popup_cancel", timeout=3000)
            self.click("help_close", timeout=2000)

            meta = doc["meta"]
            cover = resolve(self.folder, meta.get("커버사진") or "cover.jpg")
            if cover:
                def do_cover():
                    with self.page.expect_file_chooser(timeout=8000) as fc:
                        if not self.click("cover_button", timeout=4000):
                            return False
                    fc.value.set_files(str(cover))
                    self.ed.wait_for_timeout(2500)
                self.step("커버 사진", do_cover)

            def do_title():
                self.q("title").first.click()
                self.type_human(doc["title"])
            self.step("제목", do_title)

            def do_base():
                self.to_end()
                if self.click("font_family_button"):
                    self.click("font_family_option")
                if self.click("align_button"):
                    self.click("align_center_option")
            self.step("기본 서식(마루부리·가운데)", do_base)

            for i, b in enumerate(doc["blocks"]):
                t = b["type"]
                label = f"{i + 1}. {t}"
                self.log(f"→ {label}")
                if t == "text":
                    self.step(label, lambda b=b: (self.to_end(), self.write_paras(b["paras"]), self.set_bg(None), self.enter()))
                elif t == "quote":
                    self.step(label, lambda b=b: (self.to_end(), self.add_quote(b["paras"])))
                elif t in ("images", "gif"):
                    files = [resolve(self.folder, n, "gif" if t == "gif" else "image") for n in b["files"]]
                    files = [f for f in files if f]
                    if files:
                        self.step(label + " " + ", ".join(f.name for f in files),
                                  lambda files=files, b=b: (self.to_end(), self.upload_images(files, b.get("side", False)))[-1])
                    else:
                        self.result["fail"].append(label + " (파일 없음)")
                elif t == "video":
                    f = resolve(self.folder, b["file"])
                    if f:
                        self.step(label, lambda f=f, b=b: (self.to_end(), self.upload_video(f, b.get("title", "")))[-1])
                    else:
                        self.result["fail"].append(label + " (파일 없음)")
                elif t == "map":
                    self.step(label + " " + b["query"], lambda b=b: (self.to_end(), self.add_map(b["query"]))[-1])
                elif t == "divider":
                    self.step(label, lambda: (self.to_end(), self.add_divider())[-1])

            if doc["tags"]:
                def do_tags():
                    self.to_end()
                    self.enter()
                    self.apply({"size": 13, "color": "#555555", "bg": None, "bold": False})
                    for tg in doc["tags"]:
                        self.type_human("#" + tg)
                        self.page.keyboard.press("Space")
                        self.pause(0.2, 0.5)
                self.step("해시태그", do_tags)

            def do_save():
                if not self.click("save_button", timeout=8000):
                    return False
                self.page.wait_for_timeout(2500)
                self.click("publish_layer_close", timeout=1500)
            self.step("임시저장", do_save)
            self.shot("upload")
        finally:
            self.write_log(doc)
            self.page.wait_for_timeout(1500)
            ctx.close()
        return self.result

    def write_log(self, doc):
        out = self.folder / "out"
        out.mkdir(exist_ok=True)
        lines = [f"업로드 시각: {datetime.now():%Y-%m-%d %H:%M}", f"제목: {doc['title']}", "",
                 f"성공 {len(self.result['ok'])}건"] + [f"  ✓ {x}" for x in self.result["ok"]] + \
                ["", f"실패 {len(self.result['fail'])}건"] + [f"  ✗ {x}" for x in self.result["fail"]] + \
                ["", "해시태그: " + " ".join("#" + t for t in doc["tags"]),
                 "", "※ 발행은 하지 않았습니다. 네이버 임시저장함에서 확인 후 직접 발행하세요."]
        (out / "upload_log.txt").write_text("\n".join(lines), encoding="utf-8")
