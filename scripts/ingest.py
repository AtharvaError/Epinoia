#!/usr/bin/env python3
"""FestOS ingestion: parse the raw pack -> normalize -> flag -> load SQLite.

Stdlib only. Validates and FLAGS; never drops or merges. Blanks become UNKNOWN,
never a default meaning. Run:  python scripts/ingest.py
Outputs: data/festos.db  and  data/quality_report.json
"""
import csv, sqlite3, json, os, sys, datetime as dt
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC  = os.path.join(ROOT, "festos_dataset_pack")          # raw CSVs live here
OUT  = os.path.join(ROOT, "data")
DB   = os.path.join(OUT, "festos.db")

FEST_START = dt.datetime(2026, 10, 30)
FEST_END   = dt.datetime(2026, 11, 1, 23, 59)
WIN_LO, WIN_HI = dt.datetime(2026, 10, 29), dt.datetime(2026, 11, 2, 23, 59)

FILES = {  # table name -> csv file
    "participants": "participants.csv", "registrations": "registrations.csv",
    "contingents": "contingents.csv", "events": "events.csv", "venues": "venues.csv",
    "hostels": "hostels.csv", "external_stays": "external_stays.csv",
    "inventory_logistics": "inventory_logistics.csv", "inventory_merch": "inventory_merch.csv",
    "vendors": "vendors.csv", "core_team": "core_team.csv",
}

issues = []  # (entity_type, entity_id, issue, detail)
def flag(etype, eid, issue, detail=""):
    issues.append((etype, eid, issue, detail))

def parse_dt(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None  # unparseable -> caller flags

def load_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))

def create_and_fill(con, table, rows, extra_cols=()):
    """Create a table from CSV headers (all TEXT) plus extra_cols, insert rows."""
    cols = list(dict.fromkeys(list(rows[0].keys()) + list(extra_cols)))  # dedupe: extras may already be set on rows
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(f"CREATE TABLE {table} ({', '.join(f'\"{c}\" TEXT' for c in cols)})")
    con.executemany(
        f"INSERT INTO {table} ({', '.join(f'\"{c}\"' for c in cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})",
        [[r.get(c, None) for c in cols] for r in rows],
    )
    return cols

def main():
    os.makedirs(OUT, exist_ok=True)
    raw = {t: load_csv(os.path.join(SRC, f)) for t, f in FILES.items()}
    con = sqlite3.connect(DB)

    # --- enrich participants (adds normalized + flag columns) ---
    P = raw["participants"]
    name_groups = defaultdict(list)
    for r in P:
        name_groups[(r["name"].strip().lower(), r["college"].strip().lower())].append(r["participant_id"])
    for r in P:
        pid = r["participant_id"]
        g = r["gender_as_given"].strip()
        r["gender_norm"] = g if g else "UNKNOWN"
        if not g:
            flag("participant", pid, "missing_gender", "blank is not a gender")
        d = r["dietary_needs"].strip()
        r["dietary_norm"] = d if d else "UNKNOWN"
        if not d:
            flag("participant", pid, "missing_dietary", "blank is not dietary clearance")
        r["accommodation_bool"] = "1" if r["accommodation_requested"].strip().lower() == "yes" else "0"
        acc = r["accessibility_needs"].strip()
        r["needs_accessibility"] = "0" if acc in ("", "None") else "1"
        a, dep = parse_dt(r["arrival_datetime"]), parse_dt(r["departure_datetime"])
        if r["arrival_datetime"].strip() and a is None:
            flag("participant", pid, "unparseable_arrival", r["arrival_datetime"])
        if a and dep and dep < a:
            flag("participant", pid, "departure_before_arrival", f"{r['arrival_datetime']} -> {r['departure_datetime']}")
        if a and not (WIN_LO <= a <= WIN_HI):
            flag("participant", pid, "arrival_out_of_window", r["arrival_datetime"])
        key = (r["name"].strip().lower(), r["college"].strip().lower())
        if len(name_groups[key]) > 1:
            # NOT a duplicate: distinct IDs/arrivals. Review only.
            r["name_collision"] = "1"
            flag("participant", pid, "name_collision",
                 f"shares name+college with {len(name_groups[key])-1} others; distinct IDs, do not merge")
        else:
            r["name_collision"] = "0"
    create_and_fill(con, "participants", P,
        ("gender_norm", "dietary_norm", "accommodation_bool", "needs_accessibility", "name_collision"))

    # --- contingents: declared vs actual headcount ---
    actual = Counter(r["contingent_id"].strip() for r in P)
    C = raw["contingents"]
    for r in C:
        cid = r["contingent_id"].strip()
        r["strength_actual"] = str(actual.get(cid, 0))
        dec = r["strength_declared"].strip()
        if dec.isdigit() and int(dec) != actual.get(cid, 0):
            flag("contingent", cid, "strength_mismatch", f"declared {dec} vs actual {actual.get(cid,0)}")
    create_and_fill(con, "contingents", C, ("strength_actual",))

    # --- registrations: referential integrity ---
    pids = {r["participant_id"] for r in P}
    eids = {r["event_id"].strip() for r in raw["events"]}
    seen = set()
    R = raw["registrations"]
    for i, r in enumerate(R):
        if r["participant_id"].strip() not in pids:
            flag("registration", str(i), "unknown_participant", r["participant_id"])
        if r["event_id"].strip() not in eids:
            flag("registration", str(i), "unknown_event", r["event_id"])
        k = (r["participant_id"], r["event_id"])
        if k in seen:
            flag("registration", str(i), "duplicate_registration", str(k))
        seen.add(k)
    create_and_fill(con, "registrations", R)

    # --- vendors: hygiene cert status + split dietary ---
    VN = raw["vendors"]
    for r in VN:
        exp = parse_dt(r["hygiene_certificate_expiry"])
        if exp is None:
            r["cert_status"] = "unknown"
            flag("vendor", r["vendor_id"], "cert_unparseable", r["hygiene_certificate_expiry"])
        elif exp < FEST_START:
            r["cert_status"] = "expired"
            flag("vendor", r["vendor_id"], "hygiene_cert_expired", r["hygiene_certificate_expiry"])
        elif exp <= FEST_END:
            r["cert_status"] = "expires_during_fest"
            flag("vendor", r["vendor_id"], "hygiene_cert_expires_during_fest", r["hygiene_certificate_expiry"])
        else:
            r["cert_status"] = "valid"
    create_and_fill(con, "vendors", VN, ("cert_status",))

    # --- logistics: condition -> deployable ---
    IL = raw["inventory_logistics"]
    for r in IL:
        r["deployable"] = "1" if r["condition"].strip().lower() == "good" else "0"
        if r["deployable"] == "0":
            flag("logistics", r["item_id"], "not_deployable", r["condition"])
    create_and_fill(con, "inventory_logistics", IL, ("deployable",))

    # --- merch: reserved must not exceed stock (guard for live test) ---
    M = raw["inventory_merch"]
    for r in M:
        q, res = int(r["quantity"]), int(r["reserved_quantity"])
        if res > q:
            flag("merch", f"{r['item']}|{r['size']}", "reserved_exceeds_stock", f"{res}>{q}")
    create_and_fill(con, "inventory_merch", M)

    # --- plain loads (still validated via flags above where relevant) ---
    for t in ("events", "venues", "hostels", "external_stays", "core_team"):
        create_and_fill(con, t, raw[t])

    # --- issues table ---
    con.execute("DROP TABLE IF EXISTS data_issues")
    con.execute("CREATE TABLE data_issues (entity_type TEXT, entity_id TEXT, issue TEXT, detail TEXT)")
    con.executemany("INSERT INTO data_issues VALUES (?,?,?,?)", issues)
    con.commit()

    # --- derived metrics / reconciliation ---
    beds_h = sum(int(r["capacity"]) for r in raw["hostels"] if r["capacity"].strip().isdigit())
    beds_x = sum(int(r["beds"]) for r in raw["external_stays"] if r["beds"].strip().isdigit())
    accom = sum(1 for r in P if r["accommodation_bool"] == "1")
    acc_rooms = sum(1 for r in raw["hostels"] if r["accessibility"].strip().lower() == "yes")
    acc_beds = sum(int(r["capacity"]) for r in raw["hostels"]
                   if r["accessibility"].strip().lower() == "yes" and r["capacity"].strip().isdigit())
    acc_needs = sum(1 for r in P if r["needs_accessibility"] == "1")
    report = {
        "participants": len(P),
        "narrative_expected": 2400,
        "count_note": "2836 is the real count; narrative ~2400 is approximate. No ID dupes found; not reconciled down.",
        "beds_hostel": beds_h, "beds_external": beds_x, "beds_total": beds_h + beds_x,
        "accommodation_requested": accom,
        "bed_shortfall": accom - (beds_h + beds_x),
        "accessible_rooms": acc_rooms, "accessible_beds": acc_beds,
        "accessibility_needs": acc_needs,
        "accessible_bed_shortfall": acc_needs - acc_beds,
        "issues_by_type": dict(Counter(i[2] for i in issues)),
        "total_issues": len(issues),
    }
    with open(os.path.join(OUT, "quality_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    con.close()
    print(json.dumps(report, indent=2))
    return report

def _check():
    """ponytail: one runnable self-check. Fails loudly if ingestion logic breaks."""
    r = main()
    assert r["participants"] == 2836, r["participants"]
    assert r["beds_total"] == 1450, r["beds_total"]
    assert r["bed_shortfall"] > 0, "capacity pressure must be surfaced"
    assert r["issues_by_type"].get("name_collision", 0) > 0, "collisions must be flagged, not merged"
    assert r["issues_by_type"].get("departure_before_arrival", 0) == 3
    assert r["issues_by_type"].get("missing_gender", 0) == 417
    # blanks preserved, not merged: distinct IDs survive despite name collisions
    con = sqlite3.connect(DB)
    n = con.execute("SELECT COUNT(DISTINCT participant_id) FROM participants").fetchone()[0]
    con.close()
    assert n == 2836, n
    print("\nself-check OK")

if __name__ == "__main__":
    _check() if "--check" in sys.argv else main()
