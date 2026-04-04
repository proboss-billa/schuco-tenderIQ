"""Tests for services/file_classifier.py."""

import pytest
from services.file_classifier import classify_file_type


class TestClassifyFileType:
    def test_pdf_spec(self):
        assert classify_file_type("tender_spec.pdf") == "pdf_spec"

    def test_pdf_drawing_keyword(self):
        """Filenames containing drawing-related keywords should be classified as pdf_drawing."""
        assert classify_file_type("facade_drawing_rev2.pdf") == "pdf_drawing"
        assert classify_file_type("Tender Drg Set.pdf") == "pdf_drawing"
        assert classify_file_type("elevation_details.pdf") == "pdf_drawing"

    def test_docx(self):
        assert classify_file_type("spec.docx") == "docx_spec"
        assert classify_file_type("document.doc") == "docx_spec"

    def test_excel(self):
        assert classify_file_type("boq.xlsx") == "excel_boq"
        assert classify_file_type("pricing.xls") == "excel_boq"
        assert classify_file_type("data.csv") == "excel_boq"
        assert classify_file_type("sheet.ods") == "excel_boq"

    def test_cad_drawings(self):
        assert classify_file_type("floor_plan.dxf") == "dxf_drawing"
        assert classify_file_type("section.dwg") == "dwg_drawing"

    def test_unsupported(self):
        with pytest.raises(ValueError, match="Unsupported file type"):
            classify_file_type("image.png")

    def test_case_insensitive_extension(self):
        assert classify_file_type("SPEC.PDF") == "pdf_spec"
        assert classify_file_type("data.XLSX") == "excel_boq"
