from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from app.worker import enqueue

parser = argparse.ArgumentParser()
parser.add_argument(
    "job",
    choices=["create", "update", "scrape", "pipeline", "scrape_and_pipeline"],
    help="create = yangi postlar, update = tahrirlanganlar",
)
args = parser.parse_args()
print("queued:", enqueue(args.job))