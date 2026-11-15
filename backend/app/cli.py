"""Command-line entry points (used by cron / GitHub Actions and for local setup).

  python -m app.cli daily               # prices + filings + analysis + alerts for the portfolio
  python -m app.cli research-corpus TCS # index latest transcripts + annual report for one ticker
  python -m app.cli company INFY        # resolve any NSE ticker and pull its document index
"""
from __future__ import annotations

import argparse
import json
import logging

from app.core.db import SessionLocal, init_db
from app.ingestion import pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    ap = argparse.ArgumentParser(prog="finn")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("daily")
    d.add_argument("--refresh-documents", action="store_true")
    r = sub.add_parser("research-corpus")
    r.add_argument("tickers", nargs="+")
    r.add_argument("--transcripts", type=int, default=4)
    r.add_argument("--annual-reports", type=int, default=1)
    c = sub.add_parser("company")
    c.add_argument("ticker")
    w = sub.add_parser("worker")
    w.add_argument("--once", action="store_true", help="process one due job and exit")
    ra = sub.add_parser("reanalyse")
    ra.add_argument("--ticker", help="only re-analyse this company's filings")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        pipeline.seed(db)
        if args.cmd == "daily":
            run = pipeline.run_daily(db, refresh_documents=args.refresh_documents)
            print(json.dumps({"status": run.status, "stats": run.stats, "errors": run.errors}, indent=2, default=str))
        elif args.cmd == "research-corpus":
            for t in args.tickers:
                company, _ = pipeline.upsert_company(db, t)
                out = pipeline.ensure_research_corpus(db, company, transcripts=args.transcripts,
                                                      annual_reports=args.annual_reports)
                print(t, json.dumps(out, indent=2, default=str))
        elif args.cmd == "company":
            company, parsed = pipeline.upsert_company(db, args.ticker)
            pipeline.refresh_market(db, [company])
            print(company.ticker, company.company_name, len(parsed.documents), "documents,",
                  len(parsed.announcements), "announcements")
        elif args.cmd == "worker":
            from app.workers import job_worker

            print(job_worker.run_once(__import__("app.services.jobs", fromlist=["x"]).worker_id("cli"))) if args.once else job_worker.loop()
        elif args.cmd == "reanalyse":
            from app.models import Announcement

            from app.models import Company

            q = db.query(Announcement)
            if args.ticker:
                q = q.join(Company, Company.id == Announcement.company_id).filter(Company.ticker == args.ticker.upper())
            for a in q.all():
                a.processing_status = "pending"
            db.commit()
            print("analysed", pipeline.process_pending(db, limit=1000))
    finally:
        db.close()


if __name__ == "__main__":
    main()
