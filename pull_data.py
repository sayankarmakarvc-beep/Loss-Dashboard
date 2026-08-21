"""
Pulls the WSOC "Weekly Summary" tab from Google Sheets and rebuilds the same
JSON payload shape the original Apps Script (Appscript/Dashboird/Code.js
buildPayload()) produced, so index.html can stay unchanged aside from
fetching data.json instead of calling google.script.run.

Run manually, or on a schedule (Task Scheduler) to keep data.json fresh:
    python pull_data.py
"""
import sys
import os
import json
from datetime import datetime, timedelta, timezone

sys.path.insert(0, r'c:\Users\sayankarmakar.vc\Desktop\Python')
import auth_manager

import github_api

SHEET_ID = '1N5C9-oToAuD-1xZh4Fa3MCk1TpwxQPYJUI4qINIEU_w'
SHEET_TAB = 'Weekly Summary'
RM_DATA_START_ROW = 4   # 1-indexed, matches Code.js RM_DATA_START_ROW
HUB_HEADER_ROW = 62     # 0-indexed, matches Code.js HUB_HEADER_ROW
WEEK1_START = datetime(2026, 1, 5)  # matches Code.js WEEK1_START (Jan 5 2026)
MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

PRIMARY_METRICS = [
    "LM SPF loss * Overall",
    "Overall Fraud Loss (MPFBF)",
    "Untraceable (DH+ODH+MDH) * Count >1 Day",
    "PRC Non*adherance",
    "RTO/RVP Pendency (>2 Day) (DH+ODH+MDH) * count",
    "Overall Pendency(FA+NFA) *  (>2 Days) (DH+ODH+MDH)*Count",
    "Overall Retrun Pendency",
    "CV Adherence",
    "LM OBD Image Adherence",
    "Pickup accuracy",
    "Flyer ID Relatching",
    "RTO CV Image Adherence (CV Accuracy)",
]
HUB_METRIC_KEYS = ["spf", "fraud", "untrace", "prc", "rtopend", "ovpend", "retpend", "cvadh", "obdadh", "pickacc", "flyer", "rtocv"]

SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']


def parse_num(v):
    if v is None or v == '':
        return None
    s = str(v)
    for ch in ('\u20b9', ',', '%'):
        s = s.replace(ch, '')
    s = s.strip()
    if s in ('', '-'):
        return None
    try:
        return round(float(s) * 100) / 100
    except ValueError:
        return None


def get_row_count(sheets, tab):
    meta = sheets.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    for s in meta['sheets']:
        p = s['properties']
        if p['title'] == tab:
            return p['gridProperties']['rowCount']
    raise ValueError(f"Tab '{tab}' not found in spreadsheet")


def get_all_values(sheets, tab):
    row_count = get_row_count(sheets, tab)
    values = []
    chunk_size = 500
    row = 1
    while row <= row_count:
        end = min(row + chunk_size - 1, row_count)
        rng = f"'{tab}'!{row}:{end}"
        resp = sheets.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=rng, valueRenderOption='FORMATTED_VALUE'
        ).execute()
        chunk = resp.get('values', [])
        values.extend(chunk)
        if len(chunk) < (end - row + 1):
            break
        row = end + 1
    return values


def cell(row, idx):
    return row[idx] if idx < len(row) else ''


def detect_metric_groups(all_values):
    header_row = all_values[0]
    groups = []
    cur, start = None, 0
    for i, v in enumerate(header_row):
        v = (v or '').strip()
        if v:
            if cur is not None:
                groups.append({'name': cur, 'startCol': start, 'endCol': i - 1})
            cur, start = v, i
    if cur is not None:
        groups.append({'name': cur, 'startCol': start, 'endCol': len(header_row) - 1})
    return groups


def week_cols_for_group(all_values, group):
    sub_row = all_values[2]
    week_col, ytd_col = {}, None
    for c in range(group['startCol'], group['endCol'] + 1):
        label = cell(sub_row, c).strip()
        if label.isdigit():
            week_col[int(label)] = c
        elif label == 'Grand Total':
            ytd_col = c
    return week_col, ytd_col


def extract_rm_table(all_values, groups):
    weeks = list(range(1, 53))
    rm_rows, grand_total_row_idx, blank_streak = [], None, 0

    for r in range(RM_DATA_START_ROW - 1, len(all_values)):
        col_a = cell(all_values[r], 0).strip()
        col_b = cell(all_values[r], 1).strip()
        if not col_a and not col_b:
            blank_streak += 1
            if blank_streak >= 2 or grand_total_row_idx is not None:
                break
            continue
        blank_streak = 0
        if col_a == 'Grand Total' or col_b == 'Grand Total':
            grand_total_row_idx = r
            continue
        if col_b:
            rm_rows.append({'name': col_b, 'rowIdx': r})

    rm_names = [x['name'] for x in rm_rows]
    metrics = {}

    for group in groups:
        week_col, _ = week_cols_for_group(all_values, group)
        metrics[group['name']] = {}
        all_rows = list(rm_rows)
        if grand_total_row_idx is not None:
            all_rows.append({'name': 'Grand Total', 'rowIdx': grand_total_row_idx})
        for entry in all_rows:
            row = all_values[entry['rowIdx']]
            series = [parse_num(cell(row, week_col[w])) if w in week_col else None for w in weeks]
            metrics[group['name']][entry['name']] = series

    return {'weeks': weeks, 'rms': rm_names, 'metrics': metrics}


def extract_hub_table(all_values, groups):
    group_by_name = {g['name']: g for g in groups}
    metric_week_cols = [
        week_cols_for_group(all_values, group_by_name[name])[0] if name in group_by_name else {}
        for name in PRIMARY_METRICS
    ]

    rows = []
    r, blank_streak = HUB_HEADER_ROW, 0
    while r < len(all_values) and blank_streak < 3:
        row = all_values[r]
        am, hub, rm, typ, partner = (cell(row, i).strip() for i in range(5))

        if not hub:
            blank_streak += 1
            r += 1
            continue
        blank_streak = 0

        row_entry = [am, hub, rm, typ, partner]
        for week_col in metric_week_cols:
            flat = []
            for w in range(1, 53):
                if w not in week_col:
                    continue
                val = parse_num(cell(row, week_col[w]))
                if val is not None:
                    flat.extend([w, val])
            row_entry.append(flat)
        rows.append(row_entry)
        r += 1

    return {'keys': ['am', 'hub', 'rm', 'type', 'partner'] + HUB_METRIC_KEYS, 'weeks': 52, 'rows': rows}


def find_latest_week_with_data(rm_data):
    last = 0
    for rows in rm_data['metrics'].values():
        for series in rows.values():
            for i in range(len(series) - 1, last, -1):
                if series[i] is not None:
                    last = i
                    break
    return rm_data['weeks'][last]


def trim_rm_data_to_week(rm_data, latest_week):
    cutoff = rm_data['weeks'].index(latest_week) + 1
    rm_data['weeks'] = rm_data['weeks'][:cutoff]
    for rows in rm_data['metrics'].values():
        for name in rows:
            rows[name] = rows[name][:cutoff]


def build_week_month_map(num_weeks):
    return {w: (WEEK1_START + timedelta(days=7 * (w - 1))).month for w in range(1, num_weeks + 1)}


def build_payload():
    _, _, sheets = auth_manager.authenticate(SCOPES)
    all_values = get_all_values(sheets, SHEET_TAB)

    groups = detect_metric_groups(all_values)
    rm_data = extract_rm_table(all_values, groups)
    hub_data = extract_hub_table(all_values, groups)

    latest_week = find_latest_week_with_data(rm_data)
    trim_rm_data_to_week(rm_data, latest_week)
    week_month = build_week_month_map(latest_week)

    return {
        'rm': rm_data,
        'hub': hub_data,
        'weekMonth': week_month,
        'monthNames': MONTH_NAMES,
        'latestWeek': latest_week,
        'generatedAt': datetime.now(timezone.utc).isoformat(),
    }


if __name__ == '__main__':
    payload = build_payload()
    data_bytes = json.dumps(payload, ensure_ascii=False).encode('utf-8')

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')
    with open(out_path, 'wb') as f:
        f.write(data_bytes)
    print(f"Wrote local {out_path} ({len(data_bytes)} bytes, {len(payload['hub']['rows'])} hubs, latest week {payload['latestWeek']})")

    result = github_api.put_file('data.json', data_bytes, f"Auto-refresh data.json ({payload['generatedAt']})")
    print("Pushed data.json to GitHub, commit", result['commit']['sha'][:8])
