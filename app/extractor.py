"""증빙 파일(PDF/엑셀)에서 업체명·사업자번호·금액·날짜·서류종류·품목을 추출한다.

토큰을 쓰지 않는 규칙 기반 인식. 결과는 항상 사용자가 확인 화면에서 고칠 수 있다.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

DOC_TYPES = [
    ("세금계산서", ["전자세금계산서", "세금계산서"]),
    ("계산서", ["전자계산서", "계산서"]),
    ("거래명세서", ["거래명세서", "거래명세표", "납품서"]),
    ("견적서", ["견적서", "QUOTATION", "Quotation", "견 적 서"]),
    ("영수증", ["영수증", "카드전표", "매출전표", "승인번호"]),
    ("주문서", ["주문서", "발주서", "물품직접구매주문서"]),
    ("청구서", ["청구서", "인보이스", "INVOICE", "Invoice"]),
]

OWN_ORG = ["광주과학기술원", "GIST", "Gwangju Institute"]
OWN_BIZ_NO = {"220-80-02824"}
UNIT_ROW = re.compile(r"(?:^|\s)(?:ea|EA|Ea|개|set|SET|Set|box|BOX|Box|식|kit|KIT|Kit|pk|PK|pack|병|매|대|unit|UNIT)\s+(\d+)\s+[\d,]{3,}")

BIZ_NO = re.compile(r"(\d{3})\s*-\s*(\d{2})\s*-\s*(\d{5})")
DATE_PATTERNS = [
    re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})"),
]
AMOUNT_KEYS = ["합계금액", "총합계", "총 합계", "합계 금액", "총금액", "총 금액", "견적금액",
               "청구금액", "결제금액", "공급대가", "합계", "총액", "TOTAL", "Total"]
VENDOR_KEYS = ["상호", "업체명", "회사명", "공급자", "법인명"]
ITEM_KEYS = ["품명", "품목", "제품명", "Description", "규격"]


def read_text(path: str | Path) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext == ".pdf":
            import pdfplumber
            with pdfplumber.open(str(p)) as pdf:
                return "\n".join((pg.extract_text() or "") for pg in pdf.pages[:3])
        if ext in (".xlsx", ".xlsm"):
            import openpyxl
            wb = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
            out = []
            for ws in wb.worksheets[:2]:
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        out.append(" ".join(cells))
            return "\n".join(out)
        if ext in (".txt", ".csv"):
            return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return ""


def _doc_type(text: str, filename: str) -> str:
    for name, keys in DOC_TYPES:
        if any(k in filename for k in keys):
            return name
    head = text[:600]
    for name, keys in DOC_TYPES:
        if any(k in head for k in keys):
            return name
    for name, keys in DOC_TYPES:
        if any(k in text for k in keys):
            return name
    return "증빙자료"


def _to_int(s: str) -> int | None:
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None


def _amount(text: str) -> int | None:
    lines = text.splitlines()
    for key in AMOUNT_KEYS:
        for ln in lines:
            if key in ln:
                after = ln.split(key, 1)[1]
                nums = re.findall(r"[\d]{1,3}(?:,\d{3})+|\d{4,}", after)
                vals = [v for v in (_to_int(n) for n in nums) if v and v >= 100]
                if vals:
                    return max(vals)
    nums = re.findall(r"\d{1,3}(?:,\d{3})+", text)
    vals = [v for v in (_to_int(n) for n in nums) if v]
    return max(vals) if vals else None


def _date(text: str) -> str | None:
    found = []
    for pat in DATE_PATTERNS:
        for m in pat.finditer(text):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                found.append(date(y, mo, d))
            except ValueError:
                continue
    if not found:
        return None
    return found[0].isoformat()


def _clean_vendor(s: str) -> str:
    s = s.replace("|주|", "(주)").replace("ㅣ주ㅣ", "(주)")
    s = re.sub(r"\s+", " ", s).strip(" :：|·-")
    s = re.sub(r"^\((법인명|상호)\)\s*", "", s)
    s = re.split(r"(대표|성명|사업자|등록번호|주소|TEL|Tel|전화|\d{3}-\d{2}-)", s)[0]
    s = s.replace("주식회사", "(주)").strip(" :：|·-")
    return s[:30]


def _vendor(text: str, vendors: dict[str, str]) -> tuple[str | None, str | None]:
    biz_nos = [n for n in ("-".join(m.groups()) for m in BIZ_NO.finditer(text)) if n not in OWN_BIZ_NO]
    for no in biz_nos:
        if no in vendors:
            return vendors[no], no
    lines = text.splitlines()
    for key in VENDOR_KEYS:
        for ln in lines:
            if key in ln:
                cand = _clean_vendor(ln.split(key, 1)[1])
                if cand and not any(o in cand for o in OWN_ORG) and len(cand) >= 2:
                    other = next((n for n in biz_nos), None)
                    return cand, other
    m = re.search(r"((?:\(주\)|\|주\||㈜)\s*[가-힣A-Za-z0-9]+|[가-힣A-Za-z0-9]+\s*(?:\(주\)|㈜)|주식회사\s*[가-힣A-Za-z0-9]+)", text)
    if m and not any(o in m.group(0) for o in OWN_ORG):
        return _clean_vendor(m.group(0)), (biz_nos[0] if biz_nos else None)
    return None, (biz_nos[0] if biz_nos else None)


def _items(text: str) -> str | None:
    rows = []
    for ln in text.splitlines():
        for m in UNIT_ROW.finditer(ln):
            rows.append((ln, m.start()))
    if not rows:
        return None
    ln, pos = rows[0]
    name = ln[:pos].strip()
    name = re.sub(r"^(\d+\.|\d+\s+)+", "", name).strip()
    parts = name.split(" ")
    if len(parts) > 1 and re.search(r"\d", parts[-1]) and re.fullmatch(r"[\w\-#./x]+", parts[-1]):
        parts = parts[:-1]
    name = " ".join(parts).strip(" ,.")[:40]
    if not name:
        return None
    return name if len(rows) == 1 else f"{name} 외 {len(rows) - 1}종"


def short_vendor(v: str) -> str:
    v = re.sub(r"\(주\)|㈜|\|주\||주식회사|\(유\)|유한회사", "", v or "")
    return re.sub(r"\s+", " ", v).strip()


def extract(path: str | Path, vendors: dict[str, str] | None = None) -> dict:
    p = Path(path)
    text = read_text(p)
    vendor, biz = _vendor(text, vendors or {})
    result = {
        "file": str(p),
        "filename": p.name,
        "doc_type": _doc_type(text, p.name),
        "vendor": short_vendor(vendor or ""),
        "biz_no": biz or "",
        "amount": _amount(text) or 0,
        "date": _date(text) or date.today().isoformat(),
        "item": _items(text) or "",
        "text_found": bool(text.strip()),
    }
    filled = sum(1 for k in ("vendor", "amount", "item") if result[k])
    result["confidence"] = "높음" if filled == 3 else ("보통" if filled == 2 else "낮음")
    return result


if __name__ == "__main__":
    import json
    import sys
    for f in sys.argv[1:]:
        print(json.dumps(extract(f), ensure_ascii=False, indent=2))
