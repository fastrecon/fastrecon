"""Source abstraction.

Every source knows how to register itself as a DuckDB view so that the rest
of the engine can treat it as ``SELECT * FROM <view>``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import duckdb


class Source(ABC):
    """Abstract data source.

    Subclasses must implement :meth:`register` which makes ``view_name``
    queryable inside the supplied DuckDB connection.
    """

    @abstractmethod
    def register(self, con: "duckdb.DuckDBPyConnection", view_name: str) -> str:
        """Register the source as a view; return the relation SQL.

        The returned SQL is typically ``SELECT * FROM "view_name"`` but may
        wrap a subquery for SQL queries.
        """

    def describe(self) -> str:
        return self.__class__.__name__
