"""Source abstractions for fastrecon."""

from .auto import source
from .avro_file import AvroFile
from .base import Source
from .csv_file import CsvFile
from .excel_file import ExcelFile
from .fixed_width_file import FixedWidthFile
from .jdbc_query import JdbcQuery
from .json_file import JsonFile
from .mainframe_file import MainframeFile
from .odbc_query import OdbcQuery
from .orc_file import OrcFile
from .parquet_file import ParquetFile
from .sql_query import SqlQuery
from .sql_table import SqlTable
from .xml_file import XmlFile

__all__ = [
    "Source", "SqlTable", "SqlQuery", "CsvFile", "ParquetFile",
    "JsonFile", "ExcelFile", "FixedWidthFile",
    "XmlFile", "AvroFile", "OrcFile", "MainframeFile",
    "OdbcQuery", "JdbcQuery",
    "source",
]
