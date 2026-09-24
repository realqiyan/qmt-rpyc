"""In-memory xtquant mock for testing. Pure Python, no .pyd deps."""
import numpy as np
import pandas as pd

_BATCH_FAIL_SENTINEL = "__BATCH_FAIL__"


class _XtConstant:
    STOCK_BUY = 23
    STOCK_SELL = 24
    FIX_PRICE = 11
    LATEST_PRICE = 5
    ORDER_UNREPORTED = 48
    ORDER_WAIT_REPORTING = 49
    ORDER_REPORTED = 50
    ORDER_REPORTED_CANCEL = 51
    ORDER_PARTSUCC_CANCEL = 52
    ORDER_PART_CANCEL = 53
    ORDER_CANCELED = 54
    ORDER_PART_SUCC = 55
    ORDER_SUCCEEDED = 56
    ORDER_JUNK = 57
    ORDER_UNKNOWN = 255
    SH_MARKET = 0
    SZ_MARKET = 1


class StockAccount:
    def __init__(self, account_id):
        self.account_id = account_id


class XtAsset:
    def __init__(self, **kw):
        self.account_type = kw.get("account_type", 2)
        self.account_id = kw.get("account_id", "")
        self.cash = kw.get("cash", 0.0)
        self.frozen_cash = kw.get("frozen_cash", 0.0)
        self.market_value = kw.get("market_value", 0.0)
        self.total_asset = kw.get("total_asset", 0.0)


class XtOrder:
    def __init__(self, **kw):
        self.account_type = kw.get("account_type", 2)
        self.account_id = kw.get("account_id", "")
        self.stock_code = kw.get("stock_code", "")
        self.order_id = kw.get("order_id", 0)
        self.order_status = kw.get("order_status", 50)
        self.order_sysid = kw.get("order_sysid", "")
        self.order_time = kw.get("order_time", 1789696800)
        self.order_type = kw.get("order_type", 23)
        self.order_volume = kw.get("order_volume", 100)
        self.price_type = kw.get("price_type", 5)
        self.price = kw.get("price", 10.0)
        self.traded_volume = kw.get("traded_volume", 0)
        self.traded_price = kw.get("traded_price", 0.0)
        self.status_msg = kw.get("status_msg", "")
        self.strategy_name = kw.get("strategy_name", "")
        self.order_remark = kw.get("order_remark", "")


class _XtData:
    def get_market_data(self, field_list=[], stock_list=[], period="1d",
                        start_time="", end_time="", count=-1,
                        dividend_type="none", fill_data=True):
        return {s: pd.DataFrame({"open": [10.0], "close": [10.5]})
                for s in stock_list}

    def get_market_data_ex(self, field_list=[], stock_list=[], period="1d",
                           start_time="", end_time="", count=-1,
                           dividend_type="none", fill_data=True):
        columns = field_list or ['open', 'high', 'low', 'close', 'volume', 'amount',
                                  'settelementPrice', 'openInterest', 'preClose', 'suspendFlag']
        row = dict(open=10.0, high=11.0, low=9.0, close=10.5, volume=100,
                   amount=105000.0, settelementPrice=0.0, openInterest=0,
                   preClose=10.0, suspendFlag=0, time=1789660800000)
        return {code: pd.DataFrame({c: [row[c]] for c in columns},
                                  index=[20260918 if period == '1d' else 20260918145800])
                for code in stock_list}

    def get_full_tick(self, code_list):
        return {code: dict(time=1789714800000, timetag='20260918 15:00:00',
                           lastPrice=10.0, open=10.0, high=11.0, low=9.0,
                           lastClose=10.0, amount=100000.0, settlementPrice=0.0,
                           lastSettlementPrice=0.0, volume=100, pvolume=10000,
                           stockStatus=0, openInt=0, askPrice=[10.1], bidPrice=[10.0],
                           askVol=[20], bidVol=[30]) for code in code_list}

    def get_trading_calendar(self, market="SH", start_time="", end_time="", tradetimes=False):
        return ["20240101", "20240102", "20240103"]

    def get_trading_dates(self, market, start_time='', end_time='', count=-1):
        return [1789660800000]

    def download_history_data(self, stock_code, period, start_time="", end_time=""):
        return None

    def get_instrument_detail(self, stock_code, iscomplete=False):
        if stock_code == _BATCH_FAIL_SENTINEL:
            raise ValueError("mock batch failure")
        result = dict(InstrumentID=stock_code, InstrumentName="TestStock", ExchangeID='SH',
                      CreateDate='0', OpenDate='19900101', ExpireDate='99999999',
                      PreClose=10.0, SettlementPrice=0.0, UpStopPrice=11.0, DownStopPrice=9.0,
                      PriceTick=0.01, FloatVolume=1000000.0, TotalVolume=2000000.0,
                      VolumeMultiple=1, IsTrading=True)
        if iscomplete:
            result['ExtendInfo'] = dict(OptUnit=10000.0, OptExercisePrice=3.2,
                                        OptUndlCode='510050', OptUndlMarket='SH')
        return result

    def get_divid_factors(self, stock_code, start_time='', end_time=''):
        return pd.DataFrame(dict(time=[1752595200000.0], interest=[0.1], stockBonus=[0.0],
                                 stockGift=[0.0], allotNum=[0.0], allotPrice=[0.0], gugai=[0.0], dr=[1.01]),
                            index=['20250716'])

    def get_stock_list_in_sector(self, sector_name):
        return ['600000.SH']

    def get_sector_list(self):
        return ['沪深A股']

    def download_sector_data(self):
        return None

    def get_option_undl_data(self, undl_code_ref):
        return ['10000001.SH'] if undl_code_ref else {'510050.SH': ['10000001.SH']}

    def get_option_list(self, undl_code, dedate, opttype='', isavailavle=False):
        return ['10000001.SH']

    def get_option_detail_data(self, optioncode):
        return dict(ExchangeID='SHO', InstrumentID=optioncode, ExpireDate='20260923',
                    OpenDate='20260101', CreateDate='20260101', EndDelivDate='20260924',
                    OptExercisePrice=3.2, OptUnit=10000.0, OptUndlCode='510050', OptUndlMarket='SH',
                    optType='CALL', PreClose=0.1, SettlementPrice=0.1, UpStopPrice=1.0,
                    DownStopPrice=0.01, PriceTick=0.0001, VolumeMultiple=1)

    def get_financial_data(self, stock_list, table_list=[], start_time='', end_time='', report_type='report_time'):
        return {code: {table: pd.DataFrame() for table in table_list} for code in stock_list}

    def download_financial_data(self, stock_list, table_list=[], start_time='', end_time=''):
        return None

    def get_index_weight(self, index_code):
        return {'600000.SH': 1.0}

    def download_index_weight(self):
        return None


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
            "price_type": price_type,
            "price": price,
            "strategy_name": strategy_name,
            "order_remark": order_remark,
        }
        return order_id

    def cancel_order_stock(self, account, order_id):
        return 0

    def query_stock_asset(self, account):
        return XtAsset(account_id=account.account_id,
                       cash=100000.0, market_value=50000.0,
                       total_asset=150000.0)

    def query_stock_orders(self, account, cancelable_only=False):
        return [XtOrder(account_id=account.account_id, order_id=oid, **info)
                for oid, info in self._orders.items()]

    def cancel_order_stock_sysid(self, account, market, sysid):
        return 0

    def query_stock_positions(self, account):
        return []

    def query_new_purchase_limit(self, account):
        return {
            "account_id": account.account_id,
            "limit": 10000,
        }

    def echo_account_id(self, account_id):
        return account_id

    def query_account_status(self, account):
        return {"account_id": account.account_id, "status": "ok"}
