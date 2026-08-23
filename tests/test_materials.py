from __future__ import annotations

import io
import json
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from video_opinion_report.materials import (
    MaterialManifestStore,
    extract_material_archive,
    import_material_package,
    validate_material_citations,
    validate_material_package,
    validate_material_report_data,
    validate_material_report_markdown,
)


class MaterialPackageTests(unittest.TestCase):
    def make_zip(self, files: dict[str, bytes]) -> bytes:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, payload in files.items():
                archive.writestr(name, payload)
        return stream.getvalue()

    def make_docx(self, paragraphs: list[str]) -> bytes:
        document = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            + "".join(
                f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
            )
            + "</w:body></w:document>"
        ).encode("utf-8")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("word/document.xml", document)
        return stream.getvalue()

    def test_extracts_text_html_and_docx_with_source_locators(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "prepared"
            payload = self.make_zip(
                {
                    "notes/brief.txt": "第一条材料\n第二条材料".encode(),
                    "page.html": (
                        "<html><head><script>ignore me</script></head>"
                        "<body><h1>网页标题</h1><p>网页正文</p></body></html>"
                    ).encode(),
                    "memo.docx": self.make_docx(["Word 第一段", "Word 第二段"]),
                }
            )
            manifest_path = extract_material_archive(payload, destination, "素材集合.zip")
            manifest = validate_material_package(manifest_path)
            content = Path(manifest["content_path"]).read_text(encoding="utf-8")

            self.assertEqual(manifest["source_count"], 3)
            self.assertEqual(manifest["text_source_count"], 3)
            self.assertIn("[source-001#p0001]", content)
            self.assertIn("Word 第一段", content)
            self.assertIn("网页标题", content)
            self.assertNotIn("ignore me", content)
            self.assertEqual(
                {source["extraction_method"] for source in manifest["sources"]},
                {"decoded_plain_text", "visible_html_text", "docx_ooxml_text"},
            )

    def test_accepts_valid_image_as_vision_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = self.make_zip(
                {"scan.png": b"\x89PNG\r\n\x1a\n" + b"test-image-payload"}
            )
            manifest_path = extract_material_archive(
                payload, Path(directory) / "prepared", "scan.zip"
            )
            manifest = validate_material_package(manifest_path)
            self.assertEqual(manifest["image_source_count"], 1)
            self.assertEqual(manifest["image_paths"], ["files/scan.png"])
            self.assertEqual(manifest["sources"][0]["kind"], "image")
            self.assertEqual(
                manifest["sources"][0]["extraction_method"],
                "vision_model_attachment",
            )

    def test_rejects_path_traversal_duplicate_paths_and_unsupported_files(self) -> None:
        cases = (
            ({"../escape.txt": b"bad"}, "Unsafe material path"),
            ({"script.py": b"print('bad')"}, "不支持的文件"),
        )
        for index, (files, message) in enumerate(cases):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, message):
                    extract_material_archive(
                        self.make_zip(files),
                        Path(directory) / f"prepared-{index}",
                        "materials.zip",
                    )

        duplicate = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr("same.txt", b"one")
                archive.writestr("same.txt", b"two")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "重复路径"):
                extract_material_archive(
                    duplicate.getvalue(), Path(directory) / "prepared", "duplicate.zip"
                )

    def test_rejects_image_with_mismatched_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "图片格式与扩展名不匹配"):
                extract_material_archive(
                    self.make_zip({"fake.png": b"not really an image"}),
                    Path(directory) / "prepared",
                    "fake.zip",
                )

    def test_rejects_material_id_that_does_not_match_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = extract_material_archive(
                self.make_zip({"brief.txt": "来源内容".encode()}),
                Path(directory) / "prepared",
                "brief.zip",
            )
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["material_id"] = "material-brief-000000000000"
            manifest_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "素材 ID 与来源内容不一致"):
                validate_material_package(manifest_path)

    def test_import_is_idempotent_and_completes_first_two_stages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            prepared = root / "prepared"
            package_path = extract_material_archive(
                self.make_zip({"brief.txt": "需要归纳的材料".encode()}),
                prepared,
                "brief.zip",
            )
            first = import_material_package(project, package_path)
            second = import_material_package(project, package_path)
            material_id = first["material_id"]

            self.assertEqual(first["material_id"], second["material_id"])
            statuses = MaterialManifestStore(project).stage_statuses(material_id)
            self.assertEqual(statuses["ingest"], "completed")
            self.assertEqual(statuses["extract"], "completed")
            self.assertTrue(
                (project / first["artifacts"]["material_package"]).is_file()
            )

    def test_validates_report_source_coverage_and_citations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_path = extract_material_archive(
                self.make_zip({"brief.txt": "需要归纳的材料".encode()}),
                root / "prepared",
                "brief.zip",
            )
            package = validate_material_package(package_path)
            source = package["sources"][0]
            report_data = root / "report-data.json"
            report_data.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "material_id": package["material_id"],
                        "source_coverage": [
                            {
                                "source_id": source["source_id"],
                                "source_path": source["original_path"],
                                "coverage_status": "included",
                                "evidence_locations": ["第一部分 / 核心内容"],
                                "notes": "已覆盖",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            citations = root / "citations.json"
            citations.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "material_id": package["material_id"],
                        "uploaded_material": [
                            {
                                "source_id": source["source_id"],
                                "source_path": source["original_path"],
                                "sha256": source["sha256"],
                            }
                        ],
                        "external_sources": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            markdown = "\n".join(
                [
                    "# 素材报告",
                    "",
                    "## 第一部分｜素材内容整理",
                    "",
                    "> 素材说明（非原内容）：以下按来源整理。",
                    "",
                    "材料内容。" * 80,
                    "",
                    "## 第二部分｜跨素材分析与主题归纳",
                    "",
                    "综合分析。" * 30,
                    "",
                    "## 第三部分｜Agent 综合判断与待核实事项",
                    "",
                    "综合判断与限制。" * 30,
                ]
            )

            validate_material_report_markdown(markdown)
            validate_material_report_data(report_data, package)
            validate_material_citations(citations, package)

            invalid = json.loads(report_data.read_text(encoding="utf-8"))
            invalid["source_coverage"][0]["source_path"] = "wrong.txt"
            report_data.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "路径不匹配"):
                validate_material_report_data(report_data, package)


if __name__ == "__main__":
    unittest.main()
