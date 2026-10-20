"""Generate an empty AirWallet workbook; existing workbooks are preserved."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.connections.xlsx_connection import XlsxConnection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'airwallet_template.xlsx')
    args = parser.parse_args()
    if args.output.exists():
        print(f'Giữ nguyên file đã có: {args.output}')
        return 0
    return 0 if XlsxConnection.create_template(str(args.output)) else 1


if __name__ == '__main__':
    raise SystemExit(main())
