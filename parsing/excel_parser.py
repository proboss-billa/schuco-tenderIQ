# parsing/excel_parser.py

import pandas as pd
import openpyxl
from typing import List, Dict


class ExcelBOQParser:
    """Parse Excel BOQ with flexible column detection"""

    def parse(self, excel_path: str) -> List[Dict]:
        """
        Returns list of BOQ items

        Structure:
        [
            {
                'item_number': 'A.01.001',
                'description': 'Aluminum curtain wall mullion',
                'quantity': 1250.5,
                'unit': 'm',
                'rate': 350.00,
                'amount': 437675.00,
                'category': 'Facade',
                'sub_category': 'Curtain Wall'
            },
            ...
        ]
        """

        # Try to read with pandas first
        try:
            df = pd.read_excel(excel_path, sheet_name=0)
        except Exception as e:
            raise ValueError(f"Failed to read Excel: {e}")

        # Detect columns (flexible matching)
        column_mapping = self._detect_columns(df)

        if not column_mapping:
            raise ValueError("Could not detect BOQ column structure")

        # Extract items
        items = []

        for idx, row in df.iterrows():
            # Skip header rows or empty rows
            if pd.isna(row.get(column_mapping.get('description', ''), None)):
                continue

            item = {
                'item_number': self._safe_get(row, column_mapping, 'item_number'),
                'description': self._safe_get(row, column_mapping, 'description'),
                'quantity': self._safe_float(row, column_mapping, 'quantity'),
                'unit': self._safe_get(row, column_mapping, 'unit'),
                'rate': self._safe_float(row, column_mapping, 'rate'),
                'amount': self._safe_float(row, column_mapping, 'amount'),
                'category': self._safe_get(row, column_mapping, 'category'),
                'sub_category': self._safe_get(row, column_mapping, 'sub_category')
            }

            items.append(item)

        return items

    def _detect_columns(self, df: pd.DataFrame) -> Dict[str, str]:
        """Detect which columns map to which fields"""

        mapping = {}

        # Common column name variations
        patterns = {
            'item_number': ['item', 'item no', 'item number', 'sl no', 'sr no', '#'],
            'description': ['description', 'item description', 'particulars', 'work description'],
            'quantity': ['quantity', 'qty', 'qnty'],
            'unit': ['unit', 'uom', 'unit of measurement'],
            'rate': ['rate', 'unit rate', 'price'],
            'amount': ['amount', 'total', 'value'],
            'category': ['category', 'trade', 'section'],
            'sub_category': ['sub category', 'subcategory', 'sub-category']
        }

        for field, keywords in patterns.items():
            for col in df.columns:
                col_lower = str(col).lower().strip()
                if any(keyword in col_lower for keyword in keywords):
                    mapping[field] = col
                    break

        return mapping

    def _safe_get(self, row, mapping, field):
        """Safely get value from row"""
        col = mapping.get(field)
        if col and col in row.index:
            val = row[col]
            return str(val) if not pd.isna(val) else None
        return None

    def _safe_float(self, row, mapping, field):
        """Safely get numeric value"""
        col = mapping.get(field)
        if col and col in row.index:
            try:
                return float(row[col])
            except (ValueError, TypeError):
                return None
        return None