"""
build_realistic_corpus.py — generate a realistic, PHI-free Alma support corpus
whose per-(TRC, week) volumes EXACTLY match the real 52-week matrix's last 6 weeks.

  * Reproduces the 2026-05-20 Aetna/Alma rate-change spike (operational_friction::
    aetna_rate_change = 0,0,0,101,42,26 across the 6 weeks) as real rate-cut threads.
  * Plants + labels ~50 "needle" tickets where providers voice cancellation intent
    correlated to the Aetna change (cross-TRC convergence signal), in a side manifest.
  * Emits the same 21-column Zendesk-export schema as the source corpus so it ingests
    through the normal CSV path.

Run:  python scripts/build_realistic_corpus.py
Output (Downloads): alma_realistic_6wk.csv + _manifest.json + _needle.csv

Deterministic: everything is driven by --seed (default 1788).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from corpus_gen import archetypes, trc_router  # noqa: E402
from corpus_gen.entities import EntityFactory  # noqa: E402

DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")
MATRIX = os.path.join(DOWNLOADS, "trc_ticket_volume_52wk_wow.csv")
OUT_CSV = os.path.join(DOWNLOADS, "alma_realistic_6wk.csv")
OUT_MANIFEST = os.path.join(DOWNLOADS, "alma_realistic_6wk_manifest.json")
OUT_NEEDLE = os.path.join(DOWNLOADS, "alma_realistic_6wk_needle.csv")
AETNA_TRC = "operational_friction::aetna_rate_change"

HEADER = [
    "created_at",
    "Zendesk Ticket Zendesk Ticket ID",
    "Zendesk Ticket [PII Fields] Ticket Subject [PII]",
    "Zendesk Ticket Ticket Reason Code List",
    "Zendesk Ticket Status",
    "Zendesk Satisfaction (CSAT) Rating Satisfaction (CSAT) Score",
    "Zendesk Ticket Comment [PII Field] Comment Body [PII]",
    "Zendesk User (Ticket Updater) User Role",
    "Zendesk Ticket Update Details Created (EST) Raw",
    "Zendesk Ticket First Reply Time In Hours (Calendar)",
    "Zendesk Ticket Assignment To Resolution Time In Hours (Calendar)",
    "Zendesk Ticket Total Resolution Time In Hours (Calendar)",
    "insurance_payer",
    "client_id",
    "provider_id",
    "agent_id",
    "service_state",
    "channel",
    "session_date",
    "dispute_amount_usd",
    "tags",
]

FAMILY_LABEL = {
    "aetna_incident": "Aetna Rate Change", "claims": "Claims", "eligibility": "Eligibility",
    "invoicing": "Billing", "credentialing": "Credentialing", "eap": "EAP", "payout": "Payout",
    "access": "Access", "practice_mgmt": "Practice Tools", "practice_growth": "Growth",
    "member_services": "Membership", "tax": "1099", "cancellation": "Cancellation",
    "third_party": "Outreach", "catchall": "General",
}
LOW_CSAT_FAMILIES = {"aetna_incident", "cancellation", "payout"}
DAY_W = [1.0, 1.0, 1.0, 1.0, 0.9, 0.30, 0.22]            # Mon..Sun
HOUR_W = [1, 3, 5, 6, 6, 5, 5, 6, 5, 4, 3, 2, 1, 1]      # 07:00..20:00


# --------------------------------------------------------------------------
def _num(s: str) -> int:
    s = (s or "").strip().replace(",", "")
    return int(s) if s else 0


def parse_matrix(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    week_dates = [date.fromisoformat(w) for w in rows[0][1:]]
    idx = list(range(len(week_dates) - 6, len(week_dates)))
    weeks = [week_dates[i] for i in idx]
    targets, aetna = {}, None
    for r in rows[1:]:
        code = r[0]
        if code == "ALL_TRCs":
            continue
        vals = [_num(r[1 + i]) for i in idx]
        targets[code] = vals
        if code == AETNA_TRC:
            aetna = vals
    return weeks, targets, aetna


# -- timeline / metadata samplers ------------------------------------------
def open_dt(rng, week_start):
    day = rng.choices(range(7), weights=DAY_W)[0]
    hour = rng.choices(range(7, 21), weights=HOUR_W)[0]
    return datetime(week_start.year, week_start.month, week_start.day) + timedelta(
        days=day, hours=hour, minutes=rng.randint(0, 59), seconds=rng.randint(0, 59)
    )


def gap_hours(rng, first_reply):
    if first_reply:
        base = rng.choices([0.3, 0.8, 1.5, 3, 6, 12, 24, 36], weights=[3, 5, 6, 5, 4, 3, 2, 1])[0]
    else:
        base = rng.choices([0.2, 0.5, 1, 2, 4, 8, 20], weights=[4, 5, 5, 4, 3, 2, 1])[0]
    return base * rng.uniform(0.6, 1.4)


def thread_times(rng, roles, t0):
    times, seen_agent = [t0], False
    for i in range(1, len(roles)):
        first_reply = (roles[i] == "agent" and not seen_agent)
        if roles[i] == "agent":
            seen_agent = True
        times.append(times[-1] + timedelta(hours=gap_hours(rng, first_reply)))
    return times


def pick_status(rng, week_idx):
    if week_idx >= 4:  # last two weeks skew unresolved
        return rng.choices(["solved", "closed", "pending", "open"], weights=[35, 12, 28, 25])[0]
    return rng.choices(["solved", "closed", "pending", "open"], weights=[55, 25, 12, 8])[0]


def pick_csat(rng, low):
    if rng.random() < 0.55:
        return ""
    if low:
        return str(rng.choices([1, 2, 3, 4], weights=[5, 5, 3, 1])[0])
    return str(rng.choices([3, 4, 5], weights=[2, 4, 5])[0])


def pick_channel(rng):
    return rng.choices(["email", "web", "chat", "phone"], weights=[50, 25, 15, 10])[0]


def created_fmt(dt):
    return f"{dt.year}-{dt.month:02d}-{dt.day:02d} {dt.hour}:{dt.minute:02d}:{dt.second:02d}"


def est_raw(dt):
    # April–June 2026 is EDT (-04:00)
    return f"{dt.year}-{dt.month:02d}-{dt.day:02d}T{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}.000-04:00"


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1788)
    ap.add_argument("--start-id", type=int, default=600000)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    weeks, targets, aetna = parse_matrix(MATRIX)
    print(f"Matrix: {len(targets)} TRC rows, weeks {weeks[0]}..{weeks[-1]}")
    print(f"Aetna rate-change last6: {aetna}")
    spike_weeks = {w for w, v in zip(weeks, aetna) if v > 0}
    print(f"Spike weeks (aetna>0): {sorted(spike_weeks)}")

    factory = EntityFactory(rng)
    route_cache = {trc: trc_router.route(trc) for trc in targets}

    # ---- pass 1: enumerate ticket slots, assign ids -----------------------
    slots = []
    tid = args.start_id
    for trc in sorted(targets):
        fam, leaf, topic, needs_client = route_cache[trc]
        for wi, wk in enumerate(weeks):
            for _ in range(targets[trc][wi]):
                slots.append({
                    "tid": tid, "trc": trc, "family": fam, "leaf": leaf, "topic": topic,
                    "needs_client": needs_client, "week_idx": wi, "week": wk,
                    "is_needle": False, "needle_label": "",
                })
                tid += 1
    print(f"Slots (tickets): {len(slots)}")

    # ---- needle selection (45 aetna intent + 5 invoicing cross-TRC) -------
    A = [s for s in slots if s["family"] == "aetna_incident" and s["week"] in spike_weeks]
    B = [s for s in slots if s["week"] in spike_weeks and s["family"] == "invoicing"
         and ("cancellation_fee" in s["trc"] or "refund_cash_pay" in s["trc"])]
    rng.shuffle(A)
    rng.shuffle(B)
    needle = A[:45] + B[:5]
    if len(needle) < 50:
        needle += A[45:45 + (50 - len(needle))]
    for s in needle:
        s["is_needle"] = True
        s["needle_label"] = "aetna_rate_change_cancellation_intent"
    print(f"Needle tickets: {len(needle)} ({sum(1 for s in needle if s['family']=='aetna_incident')} aetna, "
          f"{sum(1 for s in needle if s['family']=='invoicing')} invoicing)")

    # ---- pass 2: realise each ticket into rows -----------------------------
    out_rows = []
    fam_counts, payer_counts = Counter(), Counter()
    gen_counts = defaultdict(int)  # (trc, week) -> ticket count, for validation
    needle_manifest = []

    for s in slots:
        t0 = open_dt(rng, s["week"])
        ctx = factory.build_ctx(
            ticket_id=s["tid"], trc=s["trc"], family=s["family"], leaf=s["leaf"],
            topic=s["topic"], week_start=s["week"], created=t0, needs_client=s["needs_client"],
        )
        ctx.is_needle = s["is_needle"]
        ctx.needle_label = s["needle_label"]
        subject, turns = archetypes.build_thread(ctx)

        roles = [r for r, _ in turns]
        times = thread_times(rng, roles, t0)
        first_agent = next((i for i, r in enumerate(roles) if r == "agent"), len(roles) - 1)
        first_reply = round((times[first_agent] - t0).total_seconds() / 3600, 1)
        total_res = round((times[-1] - t0).total_seconds() / 3600, 1)
        assign_res = round((times[-1] - times[first_agent]).total_seconds() / 3600, 1)

        status = pick_status(rng, s["week_idx"])
        low = s["is_needle"] or s["family"] in LOW_CSAT_FAMILIES or status in ("open", "pending")
        csat = pick_csat(rng, low)
        channel = pick_channel(rng)
        dispute = ctx.extra.get("dispute_amount", "")
        session_date = ctx.extra.get("session_date", "")

        base_tags = [f"provider:{ctx.provider_id}", f"payer:{ctx.payer_key}",
                     f"trc_family:{s['family']}", f"channel:{channel}"]
        if ctx.client_id:
            base_tags.append(f"client:{ctx.client_id}")
        if s["is_needle"]:
            base_tags += ["incident:aetna_rate_change_2026-05-20", "signal:cancellation_intent"]

        label = FAMILY_LABEL.get(s["family"], "General")
        for i, (role, text) in enumerate(turns):
            speaker = "provider" if role == "provider" else ("client" if role == "client" else "agent")
            author_role = "agent" if role == "agent" else "end-user"
            body = text
            if i == 0:  # bracket the opener; mark client-initiated explicitly
                body = (f"[Client · {label}] " if role == "client" else f"[{label}] ") + body
            dt = times[i]
            out_rows.append([
                created_fmt(dt),
                str(s["tid"]),
                subject,
                s["trc"],
                status,
                csat,
                body,
                author_role,
                est_raw(dt),
                f"{first_reply}",
                f"{assign_res}",
                f"{total_res}",
                ctx.payer_name,
                ctx.client_id,
                ctx.provider_id,
                ctx.agent_id if role == "agent" else "",
                ctx.state,
                channel,
                session_date,
                dispute,
                " ".join(base_tags + [f"speaker:{speaker}"]),
            ])

        fam_counts[s["family"]] += 1
        payer_counts[ctx.payer_name] += 1
        gen_counts[(s["trc"], s["week"].isoformat())] += 1
        if s["is_needle"]:
            needle_manifest.append({
                "ticket_id": s["tid"], "trc": s["trc"], "family": s["family"],
                "week_start": s["week"].isoformat(), "created_at": created_fmt(t0),
                "payer": ctx.payer_name, "provider_id": ctx.provider_id,
                "label": s["needle_label"], "subject": subject,
            })

    # ---- validate exact match ---------------------------------------------
    mismatches = []
    for trc, vals in targets.items():
        for wi, wk in enumerate(weeks):
            want, got = vals[wi], gen_counts.get((trc, wk.isoformat()), 0)
            if want != got:
                mismatches.append((trc, wk.isoformat(), want, got))
    exact = not mismatches
    print(f"\nExact per-(TRC,week) match: {exact}  ({len(mismatches)} mismatches)")
    for m in mismatches[:10]:
        print("  MISMATCH", m)

    # ---- write outputs -----------------------------------------------------
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(out_rows)

    with open(OUT_NEEDLE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(needle_manifest[0].keys()))
        w.writeheader()
        w.writerows(needle_manifest)

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "seed": args.seed,
        "source_matrix": os.path.basename(MATRIX),
        "weeks": [w.isoformat() for w in weeks],
        "total_tickets": len(slots),
        "total_rows": len(out_rows),
        "exact_match": exact,
        "mismatches": mismatches,
        "incident": {
            "name": "aetna_rate_change_2026-05-20",
            "trc": AETNA_TRC,
            "weekly_counts": dict(zip([w.isoformat() for w in weeks], aetna)),
            "description": ("Aetna notified Alma providers on 2026-05-20 of reimbursement "
                            "changes effective 2026-07-15 (extended-session premium removed; "
                            "doctoral-level E/M paid at master's rate). Volume spikes 0->101->42->26 "
                            "and correlates with membership-cancellation intent."),
            "spike_weeks": [w.isoformat() for w in sorted(spike_weeks)],
        },
        "needle": {
            "count": len(needle_manifest),
            "label": "aetna_rate_change_cancellation_intent",
            "ticket_ids": [n["ticket_id"] for n in needle_manifest],
        },
        "family_distribution": dict(fam_counts.most_common()),
        "payer_distribution": dict(payer_counts.most_common()),
    }
    with open(OUT_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nWrote {len(out_rows):,} rows / {len(slots):,} tickets -> {OUT_CSV}")
    print(f"Needle -> {OUT_NEEDLE}  ({len(needle_manifest)} tickets)")
    print(f"Manifest -> {OUT_MANIFEST}")
    print(f"\nTop families: {dict(fam_counts.most_common(6))}")
    print(f"Top payers: {dict(payer_counts.most_common(6))}")


if __name__ == "__main__":
    main()
