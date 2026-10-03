"""One-shot pipeline: import a CSV, categorize, refresh recurring detection, forecast, alert.
Usage: python run.py incoming/statement.csv [--budget 400]
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(*args):
    print(f"$ {' '.join(args)}")
    subprocess.run([sys.executable, *args], cwd=HERE, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--budget", default=None)
    args = ap.parse_args()

    run("import_csv.py", args.csv_path)
    run("categorize.py")
    run("recurring.py")
    forecast_args = ["forecast.py"] + (["--budget", args.budget] if args.budget else [])
    run(*forecast_args)
    run("alert.py")


if __name__ == "__main__":
    main()
