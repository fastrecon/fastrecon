"""fastrecon — high-performance reconciliation engine.

Public API:
    compare(left, right, keys=..., compare_mode=..., ...)
    SqlTable, SqlQuery, CsvFile, ParquetFile
    ReconConfig
    ReconResult
"""

from .api import compare
from .compare import HashCompareResult, PartitionSpec, hash_compare
from .config import ReconConfig
from .exceptions import FastreconError, SourceError, CompareError
from .output.result import ReconResult
from .sources.auto import source
from .sources.avro_file import AvroFile
from .sources.csv_file import CsvFile
from .sources.excel_file import ExcelFile
from .sources.fixed_width_file import FixedWidthFile
from .sources.jdbc_query import JdbcQuery
from .sources.json_file import JsonFile
from .sources.mainframe_file import MainframeFile
from .sources.odbc_query import OdbcQuery
from .sources.orc_file import OrcFile
from .sources.parquet_file import ParquetFile
from .sources.postgres_scanner import PostgresSource
from .sources.sql_query import SqlQuery
from .sources.sql_table import SqlTable
from .sources.xml_file import XmlFile

__all__ = [
    "compare",
    "source",
    "ReconConfig",
    "ReconResult",
    "PartitionSpec",
    "HashCompareResult",
    "hash_compare",
    "SqlTable",
    "SqlQuery",
    "CsvFile",
    "ParquetFile",
    "JsonFile",
    "ExcelFile",
    "FixedWidthFile",
    "XmlFile",
    "AvroFile",
    "OrcFile",
    "MainframeFile",
    "OdbcQuery",
    "JdbcQuery",
    "PostgresSource",
    "FastreconError",
    "SourceError",
    "CompareError",
]

__version__ = "0.9.3"
