"""In-memory xtquant mock for testing. Pure Python, no .pyd deps."""
import numpy as np
import pandas as pd

_BATCH_FAIL_SENTINEL = "__BATCH_FAIL__"


class _XtConstant:
    STOCK_BUY = 23
    STOCK_SELL = 24
    FIX_PRICE = 5
    SH_MARKET = 0
    SZ_MARKET = 1


class StockAccount:
    def __init__(self, account_id):
        self.account_id = account_id


class XtAsset:
    def __init__(self, **kw):
        self.account_id = kw.get("account_id", "")
        self.cash = kw.get("cash", 0.0)
        self.frozen_cash = kw.get("frozen_cash", 0.0)
        self.market_value = kw.get("market_value", 0.0)
        self.total_asset = kw.get("total_asset", 0.0)


class XtOrder:
    def __init__(self, **kw):
        self.account_id = kw.get("account_id", "")
        self.stock_code = kw.get("stock_code", "")
        self.order_id = kw.get("order_id", 0)
        self.order_status = kw.get("order_status", 0)


class _XtData:
    def get_market_data(self, field_list=[], stock_list=[], period="1d",
                        start_time="", end_time="", count=-1,
                        dividend_type="none", fill_data=True):
        return {s: pd.DataFrame({"open": [10.0], "close": [10.5]})
                for s in stock_list}

    def get_market_data_ex(self, field_list=[], stock_list=[], period="1d",
                           start_time="", end_time="", count=-1,
                           dividend_type="none", fill_data=True):
        return self.get_market_data(field_list, stock_list, period,
                                    start_time, end_time, count,
                                    dividend_type, fill_data)

    def get_full_tick(self, code_list):
        return {code: {"last_price": 10.0, "volume": 1000}
                for code in code_list}

    def get_trading_calendar(self, market="SH", start_time="", end_time="", tradetimes=False):
        return ["20240101", "20240102", "20240103"]

    def download_history_data(self, stock_code, period="1d",
                              start_time="", end_time=""):
        return 0

    def get_instrument_detail(self, code):
        if code == _BATCH_FAIL_SENTINEL:
            raise ValueError("mock batch failure")
        return {"InstrumentID": code, "InstrumentName": "TestStock"}


xtdata = _XtData()
xtconstant = _XtConstant()


class XtQuantTrader:
    def __init__(self, path, session_id):
        if not path:
            raise RuntimeError("path is required for XtQuantTrader")
        self._path = path
        self._session_id = session_id
        self._callback = None
        self._connected = False
        self._orders = {}

    def register_callback(self, cb):
        self._callback = cb

    def start(self):
        pass

    def connect(self):
        self._connected = True
        return 0

    def subscribe(self, account):
        return 0

    def stop(self):
        self._connected = False

    def order_stock(self, account, stock_code, order_type, order_volume,
                    price_type, price, strategy_name="", order_remark=""):
        order_id = 10000 + len(self._orders)
        self._orders[order_id] = {
            "stock_code": stock_code,
            "order_type": order_type,
            "order_volume": order_volume,
        }
        return order_id

    def cancel_order_stock(self, account, order_id):
        return 0

    def query_stock_asset(self, account):
        return XtAsset(account_id=account.account_id,
                       cash=100000.0, market_value=50000.0,
                       total_asset=150000.0)

    def query_stock_orders(self, account):
        return [XtOrder(account_id=account.account_id, order_id=oid, **info)
                for oid, info in self._orders.items()]

    def query_account_status(self, account):
        return {"account_id": account.account_id, "status": "ok"}
