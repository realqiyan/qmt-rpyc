"""Financials requests and results."""
from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional, Tuple

from .common import validate_refresh, validate_codes, validate_window

FinancialTable = Literal["Balance", "Income", "CashFlow", "Capital", "PershareIndex"]


FINANCIAL_TABLES = ("Balance", "Income", "CashFlow", "Capital", "PershareIndex")


@dataclass(frozen=True)
class BalanceRecord:
    m_timetag: date
    m_anntime: date
    tot_assets: Optional[float]
    tot_liab: Optional[float]
    tot_shrhldr_eqy_excl_min_int: Optional[float]
    total_equity: Optional[float]
    cap_stk: Optional[float]


@dataclass(frozen=True)
class IncomeRecord:
    m_timetag: date
    m_anntime: date
    revenue: Optional[float]
    oper_profit: Optional[float]
    net_profit_excl_min_int_inc: Optional[float]
    s_fa_eps_basic: Optional[float]
    s_fa_eps_diluted: Optional[float]


@dataclass(frozen=True)
class CashFlowRecord:
    m_timetag: date
    m_anntime: date
    net_cash_flows_oper_act: Optional[float]
    net_cash_flows_inv_act: Optional[float]
    net_cash_flows_fnc_act: Optional[float]
    cash_cash_equ_end_period: Optional[float]


@dataclass(frozen=True)
class CapitalRecord:
    m_timetag: date
    m_anntime: date
    total_capital: Optional[float]
    circulating_capital: Optional[float]
    restrict_circulating_capital: Optional[float]
    freeFloatCapital: Optional[float]


@dataclass(frozen=True)
class PershareIndexRecord:
    m_timetag: date
    m_anntime: date
    s_fa_bps: Optional[float]
    s_fa_ocfps: Optional[float]
    s_fa_eps_basic: Optional[float]
    s_fa_eps_diluted: Optional[float]
    du_return_on_equity: Optional[float]
    gear_ratio: Optional[float]


@dataclass(frozen=True)
class FinancialReports:
    Balance: Optional[Tuple[BalanceRecord, ...]]
    Income: Optional[Tuple[IncomeRecord, ...]]
    CashFlow: Optional[Tuple[CashFlowRecord, ...]]
    Capital: Optional[Tuple[CapitalRecord, ...]]
    PershareIndex: Optional[Tuple[PershareIndexRecord, ...]]


@dataclass(frozen=True)
class FinancialQuery:
    codes: Tuple[str, ...]
    tables: Tuple[FinancialTable, ...] = FINANCIAL_TABLES
    start: Optional[date] = None
    end: Optional[date] = None
    date_basis: Literal["report_time", "announce_time"] = "report_time"
    refresh: bool = False

    def __post_init__(self):
        validate_refresh(self.refresh)
        validate_codes(self.codes)
        validate_window(self.start, self.end)
        if any(table not in FINANCIAL_TABLES for table in self.tables):
            raise ValueError("unsupported financial table")
        if not self.tables or len(set(self.tables)) != len(self.tables):
            raise ValueError("financial tables must be nonempty and unique")
