"""final.md → 업로드 블록 목록.

지원 마커 (mazzi-blog-writing 규칙):
  메타: 제목: / 대표사진: / 커버사진: / 유형: / 사진폴더: / 강조색:
  [사진: 01.jpg]  [사진: A.jpg, B.jpg 나란히]  [GIF: g01.gif]  [동영상: video.mp4]
  [지도: 장소명]  [구분선]  [인용구 시작] ... [인용구 끝]
  ## 소제목      **굵게**      ← 붙여넣기 없이 한 글자씩 입력하고 서식은 툴바로 적용
  {노랑:텍스트} {연핑크:..} {민트:..} {하늘:..} {살구:..} {연보라:..}   ← 형광펜
  {핑크글자:..} {파랑글자:..} {보라글자:..} {초록글자:..} {주황글자:..}  ← 글자색
  #태그 #태그 (마지막 해시태그 줄)
"""
from __future__ import annotations

import re
from pathlib import Path

HIGHLIGHT = {"연핑크": "#ffe9f3", "노랑": "#ffef34", "연보라": "#fdd5f5",
             "민트": "#d4f5ec", "하늘": "#dceeff", "살구": "#ffe4cc"}
TEXTCOLOR = {"핑크글자": "#ff65a8", "파랑글자": "#0078cb", "보라글자": "#8e5cd9",
             "초록글자": "#1fa67a", "주황글자": "#ff8a3d"}
HEADING_COLORS = ["#ff65a8", "#0078cb", "#8e5cd9", "#1fa67a", "#ff8a3d"]
META_KEYS = ("제목", "대표사진", "커버사진", "유형", "사진폴더", "강조색")

def _segments(text: str, size: int, color: str, bold: bool) -> list[dict]:
    """인라인 마커를 서식 조각 목록으로 나눈다. 각 조각은 직접 타이핑 + 툴바 서식으로 입력된다."""
    segs = []
    pos = 0
    pat = re.compile(r"\{([가-힣]+):(.+?)\}|\*\*(.+?)\*\*")
    base = {"size": size, "color": color, "bg": None, "bold": bold}
    for m in pat.finditer(text):
        if m.start() > pos:
            segs.append({**base, "text": text[pos:m.start()]})
        if m.group(3) is not None:
            segs.append({**base, "text": m.group(3), "bold": True})
        else:
            key, body = m.group(1), m.group(2)
            body = body.replace("**", "")
            if key in HIGHLIGHT:
                segs.append({**base, "text": body, "bg": HIGHLIGHT[key]})
            elif key in TEXTCOLOR:
                segs.append({**base, "text": body, "color": TEXTCOLOR[key], "bold": True})
            else:
                segs.append({**base, "text": body})
        pos = m.end()
    if pos < len(text):
        segs.append({**base, "text": text[pos:]})
    return [x for x in segs if x["text"]]


def para(text: str, size: int = 13, color: str = "#333333", bold: bool = False) -> dict:
    return {"segments": _segments(text, size, color, bold) if text else []}


def parse(md_path: str | Path) -> dict:
    p = Path(md_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    meta, blocks, tags = {}, [], []
    text_buf: list[str] = []
    quote_buf: list[str] | None = None
    h_idx = 0

    def flush():
        if text_buf:
            while text_buf and not text_buf[-1]["segments"]:
                text_buf.pop()
            if text_buf:
                blocks.append({"type": "text", "paras": list(text_buf)})
            text_buf.clear()

    body_started = False
    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        # 메타 줄 (본문 시작 전)
        if not body_started:
            m = re.match(r"^(%s)\s*[:：]\s*(.*)$" % "|".join(META_KEYS), s)
            if m:
                meta[m.group(1)] = m.group(2).strip()
                continue
            if not s:
                continue
            body_started = True

        # 인용구
        if s.startswith("[인용구 시작"):
            flush()
            quote_buf = []
            continue
        if s.startswith("[인용구 끝"):
            qh = []
            for i, q in enumerate(quote_buf or []):
                if not q:
                    qh.append(para(""))
                elif i == 0 or q.startswith("📌"):
                    qh.append(para(q, 15, "#ff65a8", True))
                elif re.match(r"^Q[.\s]", q):
                    qh.append(para(q, 13, "#333333", True))
                elif re.match(r"^A[.\s]", q):
                    hl = re.sub(r"(\d[\d,.:~\-]*\s*(시간|개월|원|분|시|세|개|km|%|대|명|일|회)?)",
                                r"{노랑:\1}", q)
                    qh.append(para(hl))
                else:
                    qh.append(para(q))
            blocks.append({"type": "quote", "paras": qh})
            quote_buf = None
            continue
        if quote_buf is not None:
            quote_buf.append(s)
            continue

        # 컴포넌트 마커
        m = re.match(r"^\[(사진|GIF|동영상|지도)\s*[:：]\s*(.+?)\]$", s)
        if m:
            flush()
            kind, arg = m.group(1), m.group(2)
            if kind == "사진":
                side = "나란히" in arg
                names = [n.strip() for n in arg.replace("나란히", "").split(",") if n.strip()]
                blocks.append({"type": "images", "files": names, "side": side and len(names) > 1})
            elif kind == "GIF":
                blocks.append({"type": "gif", "files": [arg.strip()]})
            elif kind == "동영상":
                name, _, title = arg.partition("|")
                blocks.append({"type": "video", "file": name.strip(), "title": title.strip()})
            else:
                blocks.append({"type": "map", "query": arg.strip()})
            continue
        if s == "[구분선]":
            flush()
            blocks.append({"type": "divider"})
            continue
        # 해시태그 줄
        if re.fullmatch(r"(#[^\s#]+\s*){2,}", s):
            tags = re.findall(r"#([^\s#]+)", s)
            continue
        # 소제목
        if s.startswith("## ") or s.startswith("### "):
            title = s.lstrip("#").strip()
            text_buf.append(para(title, 19, HEADING_COLORS[h_idx % len(HEADING_COLORS)], True))
            h_idx += 1
            continue
        if s.startswith("# "):
            text_buf.append(para(s[2:].strip(), 24, "#333333", True))
            continue
        # 일반 문단 (빈 줄 = 줄바꿈)
        text_buf.append(para(s))

    flush()
    return {"meta": meta, "blocks": blocks, "tags": tags, "title": meta.get("제목", "")}


def resolve(folder: Path, name: str, kind: str = "image") -> Path | None:
    """마커 파일명을 실제 경로로 찾는다 (edited/, edited/gif/, 폴더 전체 순)."""
    cands = []
    if kind == "gif":
        cands += [folder / "edited" / "gif" / name, folder / "gif" / name]
    cands += [folder / "edited" / name, folder / name, folder / "out" / name]
    for c in cands:
        if c.exists():
            return c
    hits = list(folder.rglob(name))
    return hits[0] if hits else None


if __name__ == "__main__":
    import json
    import sys
    r = parse(sys.argv[1])
    for b in r["blocks"]:
        if "paras" in b:
            print(b["type"], [[(g["text"][:20], g["size"], g["color"], g["bg"], g["bold"]) for g in p["segments"]] for p in b["paras"][:3]])
        else:
            print(b)
    print("meta", r["meta"], "tags", r["tags"])
