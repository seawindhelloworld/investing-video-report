# Material report contract

## Draft files

Create exactly these draft files in the generated directory supplied by the runner:

- `report.md`
- `report-data.json`
- `citations.json`

Do not render HTML, alter the imported package, operate Git, or write outside that generated directory.

## Markdown

The report must contain the following H2 headings once, in this order:

```markdown
## 第一部分｜素材内容整理
## 第二部分｜跨素材分析与主题归纳
## 第三部分｜Agent 综合判断与待核实事项
```

Include the exact phrase `素材说明（非原内容）` in a visually isolated report note. Cite uploaded material using the source IDs and file names from `material-package.json`. For extracted text, use paragraph locators such as `source-001#p0001` wherever practical. For an image, note that its text was read visually and preserve uncertain characters.

## `report-data.json`

Use a JSON object with at least:

```json
{
  "schema_version": 1,
  "material_id": "material-id-from-package",
  "source_coverage": [
    {
      "source_id": "source-001",
      "source_path": "original/path.txt",
      "coverage_status": "included",
      "evidence_locations": ["第一部分 / 主题标题"],
      "notes": "覆盖范围或读取限制"
    }
  ]
}
```

Every package source must appear exactly once. `coverage_status` is `included` or, only when the content truly cannot be read, `no_readable_text`. An included source needs at least one report location. A source with programmatically extracted text cannot be marked unreadable.

Additional topic, relationship, contradiction, inference, confidence, and verification fields are allowed when they remain valid JSON and preserve the required coverage records.

## `citations.json`

Use a JSON object with this base shape:

```json
{
  "schema_version": 1,
  "material_id": "material-id-from-package",
  "uploaded_material": [
    {
      "source_id": "source-001",
      "source_path": "original/path.txt",
      "sha256": "hash-from-material-package"
    }
  ],
  "external_sources": []
}
```

Every uploaded source must appear exactly once with the path and SHA-256 from the package. When external research is used, each `external_sources` entry must contain at least `title`, a direct `url`, and `accessed_at`; add the claim and applicability when useful. Leave the array empty when no external research is needed.
