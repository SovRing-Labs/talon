"""One-off: price Vertex sessions recorded before TALON-VERTEX-OPS-0 (2026-10-04), when cost was stored as 0.

Those rows have only session totals: no per-call prompt sizes (so no 200k tier) and no thinking tokens
(Vertex reports them outside completion_tokens). The figure is therefore a LOWER BOUND, and the session
says so in a notice. Idempotent: only sessions with cost = 0 and a priced lane are touched.

  python3 -m engine.reprice [--apply]      (TALON_DB selects the database)
"""

from __future__ import annotations

import sys

from .providers import lane_price
from .sessions import SessionStore


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    st = SessionStore()
    total = 0.0
    try:
        for s in st.conn.execute("SELECT id, lane, tokens_in, tokens_out FROM sessions WHERE cost = 0 "
                                 "AND (tokens_in > 0 OR tokens_out > 0)").fetchall():
            price = lane_price(s["lane"] or "")
            if not price:
                continue
            est = (s["tokens_in"] * float(price.get("input") or 0) + s["tokens_out"] * float(price.get("output") or 0)) / 1e6
            total += est
            print(f"{s['id']}  {s['lane']:<36} {s['tokens_in']:>11,} in {s['tokens_out']:>9,} out  ≥ ${est:,.2f}")
            if apply:
                st.conn.execute("UPDATE sessions SET cost=? WHERE id=?", (est, s["id"]))
                st.conn.execute("UPDATE messages SET cost=? WHERE session_id=? AND seq=(SELECT max(seq) FROM messages "
                                "WHERE session_id=? AND role='assistant')", (est, s["id"], s["id"]))
                st.conn.commit()
                st.append(s["id"], {"role": "notice", "name": "info", "content":
                          f"spend backfilled 2026-10-04: ≥ ${est:,.2f} — a lower bound (recorded before thinking tokens "
                          f"and the >200k price tier were counted)"})
        print(f"total ≥ ${total:,.2f}{'' if apply else '  (dry run; --apply to write)'}")
    finally:
        st.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
