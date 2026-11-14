/**
 * 지출 건 도우미 ↔ 연구비 구글시트 연결용 Apps Script
 * 설치: 시트 메뉴 [확장 프로그램] → [Apps Script] → 이 코드 붙여넣기 → setup 실행 → 웹앱 배포
 * 프로그램에는 웹앱 주소와 setup 실행 시 나온 연결키만 입력하면 된다.
 */

var SETTINGS_TAB = '설정';

function setup() {
  var props = PropertiesService.getScriptProperties();
  var token = props.getProperty('TOKEN');
  if (!token) {
    token = Utilities.getUuid().replace(/-/g, '').slice(0, 20);
    props.setProperty('TOKEN', token);
  }
  Logger.log('연결키: ' + token);
  return token;
}

function doPost(e) {
  try {
    var req = JSON.parse(e.postData.contents || '{}');
    var token = PropertiesService.getScriptProperties().getProperty('TOKEN');
    if (!token || req.token !== token) return out_({ ok: false, error: '연결키가 맞지 않습니다.' });
    var lock = LockService.getScriptLock();
    lock.waitLock(20000);
    try {
      var data;
      if (req.action === 'settings') data = readSettings_();
      else if (req.action === 'append') data = appendRow_(req.row);
      else if (req.action === 'pending') data = pending_();
      else if (req.action === 'update') data = update_(req);
      else return out_({ ok: false, error: '알 수 없는 요청' });
      return out_({ ok: true, data: data });
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return out_({ ok: false, error: String(err && err.message || err) });
  }
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

/* ---------- 설정 탭 ---------- */
function readSettings_() {
  var sh = SpreadsheetApp.getActive().getSheetByName(SETTINGS_TAB);
  var v = sh.getDataRange().getDisplayValues();
  var tasks = [], semoks = [], choices = {};
  for (var r = 0; r < v.length; r++) {
    var row = v[r];
    if (row[1] === '과제구분') {
      for (var c = 2; c < row.length; c++) {
        var key = row[c];
        if (['결제수단', '진행상황', '이지바로', 'ZEUS(구매 등)', 'ZEUS(회의비)'].indexOf(key) >= 0) {
          var list = [];
          for (var k = r + 1; k < v.length && k < r + 40; k++) {
            if (!v[k][c]) break;
            list.push(v[k][c]);
          }
          choices[key] = list;
        }
      }
      for (var t = r + 1; t < v.length && v[t][1]; t++) {
        tasks.push({ name: v[t][1], lead: v[t][2], title: v[t][3], type: v[t][4] });
      }
    }
    if (row[1] === '세목' && row[2] === '예산항목') {
      for (var s = r + 1; s < v.length && v[s][1]; s++) {
        semoks.push({ semok: v[s][1], budget_item: v[s][2], bimok: v[s][3], code: v[s][4] || '' });
      }
    }
  }
  return { tasks: tasks, semoks: semoks, choices: choices };
}

/* ---------- 과제 탭 집행 내역 표 ---------- */
function table_(task) {
  var sh = SpreadsheetApp.getActive().getSheetByName(task);
  if (!sh) throw new Error('과제 탭이 없습니다: ' + task);
  var v = sh.getDataRange().getDisplayValues();
  for (var r = 0; r < v.length; r++) {
    if (v[r][1] === 'No.' && v[r][2] === '사용일자') {
      var cols = {};
      v[r].forEach(function (h, i) {
        h = String(h).replace(/\n/g, ' ');
        if (h.indexOf('사용일자') === 0) cols.date = i;
        else if (h.indexOf('세목') === 0) cols.semok = i;
        else if (h.indexOf('집행 내역') === 0) cols.usage = i;
        else if (h.indexOf('금액') === 0) cols.amount = i;
        else if (h.indexOf('결제수단') === 0) cols.pay = i;
        else if (h.indexOf('증빙자료') === 0) cols.memo = i;
        else if (h === 'ZEUS') cols.zeus = i;
        else if (h === '이지바로') cols.ezbaro = i;
        else if (h.indexOf('진행상황') === 0) cols.progress = i;
        else if (h === 'No.') cols.no = i;
      });
      return { sheet: sh, header: r, cols: cols, values: v };
    }
  }
  throw new Error('집행 내역 표를 찾지 못했습니다: ' + task);
}

function toDate_(iso) {
  var p = String(iso || '').split('-');
  return p.length === 3 ? new Date(+p[0], +p[1] - 1, +p[2]) : iso;
}

function appendRow_(d) {
  var t = table_(d.task), c = t.cols, v = t.values, maxNo = 0, target = -1;
  for (var r = t.header + 1; r < v.length; r++) {
    var n = parseInt(v[r][c.no], 10);
    var empty = !v[r][c.date] && !v[r][c.usage] && !v[r][c.amount];
    if (!empty && !isNaN(n)) maxNo = Math.max(maxNo, n);
    if (empty && target < 0) target = r;
    if (empty && target >= 0) break;
  }
  if (target < 0) target = v.length;
  for (var k = t.header + 1; k < target; k++) {
    if (v[k][c.usage] === d.usage && String(v[k][c.amount]).replace(/,/g, '') === String(d.amount)) {
      throw new Error('같은 집행 건이 이미 있습니다 (' + (k + 1) + '행).');
    }
  }
  var sh = t.sheet, row = target + 1;
  var set = function (col, val) { if (col !== undefined && val !== undefined && val !== '') sh.getRange(row, col + 1).setValue(val); };
  if (!v[target] || !v[target][c.no]) set(c.no, maxNo + 1);
  set(c.date, toDate_(d.date));
  set(c.semok, d.semok);
  set(c.usage, d.usage);
  set(c.amount, Number(d.amount) || d.amount);
  set(c.pay, d.pay);
  set(c.memo, d.memo);
  set(c.zeus, d.zeus || '미신청');
  set(c.ezbaro, d.ezbaro || '미결의');
  set(c.progress, d.progress || 'ZEUS 구매신청');
  return { row: row, no: maxNo + 1 };
}

function pending_() {
  var s = readSettings_(), list = [];
  s.tasks.forEach(function (tk) {
    var t;
    try { t = table_(tk.name); } catch (e) { return; }
    var c = t.cols, v = t.values;
    for (var r = t.header + 1; r < v.length; r++) {
      if (!v[r][c.usage] && !v[r][c.amount]) continue;
      if (v[r][c.ezbaro] === '집행' && v[r][c.progress] === '완료') continue;
      list.push({
        task: tk.name, row: r + 1, date: v[r][c.date], semok: v[r][c.semok], usage: v[r][c.usage],
        amount: v[r][c.amount], memo: v[r][c.memo], zeus: v[r][c.zeus], ezbaro: v[r][c.ezbaro], progress: v[r][c.progress]
      });
    }
  });
  return list;
}

function update_(q) {
  var t = table_(q.task), c = t.cols, sh = t.sheet;
  if (q.zeus) sh.getRange(q.row, c.zeus + 1).setValue(q.zeus);
  if (q.ezbaro) sh.getRange(q.row, c.ezbaro + 1).setValue(q.ezbaro);
  if (q.progress) sh.getRange(q.row, c.progress + 1).setValue(q.progress);
  return { row: q.row };
}
