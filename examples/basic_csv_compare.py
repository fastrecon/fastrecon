"""Smallest possible end-to-end example: compare two CSV files."""

from pathlib import Path

from fastrecon import CsvFile, compare

DATA = Path(__file__).parent / "data"
left = DATA / "orders_left.csv"
right = DATA / "orders_right.csv"

left.write_text(
    "order_id,sku,amount\n"
    "1,A,10.00\n"
    "2,B,20.00\n"
    "3,C,30.00\n"
    "4,D,40.00\n"
)
right.write_text(
    "order_id,sku,amount\n"
    "1,A,10.00\n"
    "2,B,25.00\n"   # changed
    "4,D,40.00\n"   # 3 missing
    "5,E,50.00\n"   # extra
)

result = compare(
    left=CsvFile(str(left)),
    right=CsvFile(str(right)),
    keys=["order_id"],
    tolerances={"amount": 0.001},
)

print(result.summary())
print()
print(result.to_json(indent=True))
