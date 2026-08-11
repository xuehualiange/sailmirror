import json
import re
import sys
from pathlib import Path

import openpyxl

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = Path(__file__).parent
EXCEL_FILE = SCRIPT_DIR / "规则库填写.xlsx"
OUTPUT_FILE = SCRIPT_DIR / "knowledge" / "culture_rules_v1.json"

FIELDS = ["id", "market", "category", "rule", "severity", "source", "source_level", "source_url"]

COLUMN_ALIASES = {
    "id": {"id", "编号", "规则id", "规则编号", "序号", "rule_id", "rule id"},
    "market": {"market", "市场", "目标市场", "适用市场", "target market"},
    "category": {"category", "类别", "分类", "规则类别", "规则分类", "类型"},
    "rule": {"rule", "规则", "规则内容", "规则描述", "检测规则", "rule content", "description"},
    "severity": {"severity", "严重程度", "风险等级", "等级", "级别", "risk level"},
    "source": {"source", "来源", "规则来源", "依据", "reference"},
    "source_level": {"source_level", "来源等级", "来源级别", "溯源等级", "level", "source level"},
    "source_url": {"source_url", "来源链接", "链接", "url", "source url", "参考链接"},
}

SKIP_PATTERNS = re.compile(
    r"^(【|fields\b|field\b|说明|示例|注意|填写|模板|template\b|instructions?\b|"
    r"字段说明|使用说明|版本|version\b)",
    re.IGNORECASE,
)


def normalize_header(value) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


FIELD_HEADER_SET = set()
for aliases in COLUMN_ALIASES.values():
    FIELD_HEADER_SET.update(normalize_header(alias) for alias in aliases)


def should_skip_row(row_values: list) -> bool:
    non_empty = [normalize_cell(v) for v in row_values if normalize_cell(v)]
    if not non_empty:
        return True

    first = non_empty[0]
    if SKIP_PATTERNS.search(first):
        return True
    if first in {"Fields", "Field", "字段", "列名", "Header"}:
        return True
    return False


def map_header_to_field(header: str):
    normalized = normalize_header(header)
    for field, aliases in COLUMN_ALIASES.items():
        if normalized in {normalize_header(alias) for alias in aliases}:
            return field
    return None


def find_header_row(rows: list[list]) -> tuple[int, dict[int, str]]:
    for idx, row in enumerate(rows):
        mapping = {}
        for col_idx, cell in enumerate(row):
            field = map_header_to_field(cell)
            if field:
                mapping[col_idx] = field
        if {"id", "market", "rule"} <= set(mapping.values()):
            return idx, mapping
        if len(mapping) >= 4 and "rule" in mapping.values():
            return idx, mapping
    raise ValueError("未找到有效标题行，请确认 Excel 包含 id / market / rule 等列名")


def row_to_record(row_values: list, column_mapping: dict[int, str]) -> dict | None:
    record = {field: "" for field in FIELDS}
    for col_idx, field in column_mapping.items():
        if col_idx < len(row_values):
            record[field] = normalize_cell(row_values[col_idx])

    if not record["id"] or not record["rule"]:
        return None
    if normalize_header(record["id"]) in FIELD_HEADER_SET:
        return None
    if SKIP_PATTERNS.search(record["id"]) or SKIP_PATTERNS.search(record["rule"]):
        return None
    return record


def convert_excel_to_json() -> list[dict]:
    if not EXCEL_FILE.exists():
        raise FileNotFoundError(f"未找到 Excel 文件：{EXCEL_FILE}")

    workbook = openpyxl.load_workbook(EXCEL_FILE, data_only=True)
    sheet = workbook.worksheets[0]

    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    header_idx, column_mapping = find_header_row(rows)

    records = []
    for row in rows[header_idx + 1 :]:
        if should_skip_row(row):
            continue
        record = row_to_record(row, column_mapping)
        if record:
            records.append(record)

    return records


def print_stats(records: list[dict]) -> None:
    market_counts: dict[str, int] = {}
    for record in records:
        market = record["market"] or "未指定"
        market_counts[market] = market_counts.get(market, 0) + 1

    print(f"总条数：{len(records)}")
    print("各市场条数：")
    for market in sorted(market_counts, key=lambda x: (-market_counts[x], x)):
        print(f"  - {market}: {market_counts[market]}")


def main() -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    records = convert_excel_to_json()
    OUTPUT_FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"已保存：{OUTPUT_FILE}")
    print_stats(records)


if __name__ == "__main__":
    main()
