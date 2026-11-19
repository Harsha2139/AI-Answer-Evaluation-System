"""Create real .xlsx workbooks from pandas DataFrames (openpyxl)."""
from io import BytesIO

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

_HEADER_FILL = PatternFill("solid", fgColor="0B1F3A")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TITLE_FONT = Font(bold=True, size=12, color="0B1F3A")


def _cell_value(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def workbook_bytes(sheets: dict) -> bytes:
    """sheets: {sheet_name: DataFrame | [(block_title, DataFrame), ...]}"""
    wb = Workbook()
    wb.remove(wb.active)
    for name, content in sheets.items():
        ws = wb.create_sheet(title=name[:31])
        blocks = content if isinstance(content, list) else [(None, content)]
        row = 1
        for title, df in blocks:
            if title:
                ws.cell(row=row, column=1, value=title).font = _TITLE_FONT
                row += 1
            for c, col in enumerate(df.columns, start=1):
                cell = ws.cell(row=row, column=c, value=str(col))
                cell.fill, cell.font = _HEADER_FILL, _HEADER_FONT
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            if row == 1 or (title and row == 2):
                ws.freeze_panes = ws.cell(row=row + 1, column=1)
            row += 1
            for rec in df.itertuples(index=False):
                for c, v in enumerate(rec, start=1):
                    ws.cell(row=row, column=c, value=_cell_value(v)).alignment = Alignment(
                        vertical="top", wrap_text=isinstance(v, str) and len(v) > 40)
                row += 1
            row += 1  # blank line between blocks
        for idx, col_cells in enumerate(ws.columns, start=1):
            longest = max((len(str(c.value)) for c in col_cells if c.value is not None), default=8)
            ws.column_dimensions[get_column_letter(idx)].width = min(max(12, longest + 2), 60)
    if not wb.sheetnames:
        wb.create_sheet("Empty")
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
