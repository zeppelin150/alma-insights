"""One-time Guru HTML style round-trip probe (opt-in, LIVE credentials).

Guru's content API normalizes submitted HTML into its own block model and
publishes no allow-list. Text color + highlight are evidenced to survive
(Guru stores callouts/colors as inline-style HTML), but `text-align`,
bare `<u>`/`text-decoration`, and `font-family` are OUTSIDE Guru's editor
vocabulary and may be stripped. The only way to know for YOUR Guru is to
POST a card with the full style matrix, GET it back, and diff.

Run this manually with live Guru creds configured (Settings → Connections):

    python scripts/guru_style_roundtrip_probe.py --collection <COLLECTION_ID>

It creates ONE throwaway card, fetches it, and prints which style
properties Guru retained. Use the result to decide whether to enable the
paragraph-alignment control in the rich editor (color/highlight are
already enabled; alignment is intentionally held pending this probe).

Nothing here runs in CI — it needs the network and real credentials.
"""

from __future__ import annotations

import argparse
import re
import sys

_STYLE_MATRIX = (
    '<p style="text-align:center">'
    '<span style="color:#cc0000;background-color:#fff2a8">'
    'colored + highlighted + centered</span></p>'
    '<p><span style="text-decoration:underline">underlined</span> and '
    '<span style="font-family:monospace">monospace</span></p>'
    '<table border="1" cellspacing="0" cellpadding="4">'
    '<tr><td bgcolor="#0d7d72">cell A</td><td>cell B</td></tr></table>'
)

_PROBES = {
    "color": r"color:\s*#?cc0000",
    "highlight (background-color)": r"background-color:\s*#?fff2a8",
    "text-align": r"text-align:\s*center",
    "underline (text-decoration)": r"text-decoration:\s*underline",
    "font-family": r"font-family",
    "table bgcolor": r"bgcolor=.?#?0d7d72|background-color:\s*#?0d7d72",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collection", required=True, help="Guru collection id")
    ap.add_argument("--title", default="[probe] style round-trip — safe to delete")
    args = ap.parse_args()

    from src.data.guru_client import GuruClient
    client = GuruClient.from_settings() if hasattr(GuruClient, "from_settings") else None
    if client is None or not getattr(client, "is_configured", False):
        print("Guru is not configured (Settings → Connections). Aborting.")
        return 2

    print("Creating throwaway probe card…")
    created = client.create_card(args.collection, args.title, _STYLE_MATRIX)
    card_id = created.get("id") if isinstance(created, dict) else None
    if not card_id:
        print(f"Create failed: {created!r}")
        return 1
    print(f"  card id: {card_id}")

    fetched = client.get_card(card_id)
    returned = (fetched or {}).get("content", "")
    print("\n── What Guru retained (returned card HTML) ──")
    for label, pat in _PROBES.items():
        kept = bool(re.search(pat, returned, re.IGNORECASE))
        print(f"  {'KEPT  ' if kept else 'DROPPED'}  {label}")
    print("\nReturned content (first 1200 chars):\n")
    print(returned[:1200])
    print(f"\nDelete the probe card in Guru when done (id {card_id}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
