"""Persistent data invariants, using synthetic providers and real SQLite."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import sqlite3
import threading
import time

import pytest

from qmt_rpyc.adapters.interfaces import Providers
from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.contracts.common import BatchResult, CachedCodesRequest, RefreshRequest, Success, Failure, ItemError
from qmt_rpyc.contracts.financials import FinancialQuery, FinancialReports, BalanceRecord
from qmt_rpyc.contracts.instruments import Instrument, KnownDate, DatePlaceholder
from qmt_rpyc.contracts.market import DailyBar, DailyBarSeries, DailyBarsQuery, TradingDatesRequest
from qmt_rpyc.contracts.options import ExpiryDates, ExpiryDatesRequest, OptionContract
from qmt_rpyc.contracts.reference import DividendEvent, DividendQuery
from qmt_rpyc.contracts.system import Capabilities, Capability
from qmt_rpyc.contracts.operations import OPERATIONS
from qmt_rpyc.storage.coverage import Evidence, ConservativeEvidence, gaps
from qmt_rpyc.storage.policy import Freshness, StorageConfig, SHANGHAI
from qmt_rpyc.storage.repository import Repository, Partition, Write
from qmt_rpyc.storage.service import PersistentData
from qmt_rpyc.storage.adjustment import SampledBigQmtAdjustment
from qmt_rpyc.storage.providers import decorate

NOW = datetime(2026, 9, 29, 1, tzinfo=timezone.utc)
START, END = date(2026,9,21), date(2026,9,25)


def bar(day, close=10.):
    return DailyBar(day,None,close,close,close,close,close,100,1000.,0,0,0.)


def instrument(name='name'):
    return Instrument('SH','600000',name,DatePlaceholder('0'),KnownDate(START),DatePlaceholder('0'),1.,2.,100,None)


class Proven(ConservativeEvidence):
    """Synthetic source explicitly promises its generated finite universe."""
    def market_for(self, code):
        return 'SH'

    def assess(self, dataset, request, rows, start, end):
        return Evidence(True,'synthetic finite universe')


class Source:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.rows = tuple(bar(START+timedelta(days=i)) for i in range(5))
        self.events = ()
        self.balance = (BalanceRecord(START,START,1.,2.,3.,4.,5.),)
        self.report_failure = False
        self.name = 'name'

    def record(self, method, request):
        self.calls.append((method,request))
        if self.fail:
            raise ProviderError('NOT_CONNECTED',method,'offline')

    def get_details(self, request):
        self.record('details',request)
        return BatchResult(tuple(Success(code,instrument(self.name)) for code in request.codes))

    def get_contract_details(self, request):
        self.record('contracts',request)
        return BatchResult(tuple(Success(code,OptionContract('510050.SH','option','CALL',END,1.,10000)) for code in request.codes))

    def list_option_underlyings(self, request):
        self.record('underlyings',request)
        return ('510050.SH',)

    def get_expiry_dates(self, request):
        self.record('expiry',request)
        return ExpiryDates(NOW.astimezone(SHANGHAI).date(),(date(2026,10,28),))

    def get_trading_dates(self, request):
        self.record('calendar',request)
        return tuple(row.trade_date for row in self.rows if (not request.start or row.trade_date>=request.start)
                     and (not request.end or row.trade_date<=request.end))

    def get_dividend_events(self, request):
        self.record('events',request)
        return tuple(row for row in self.events if (not request.start or row.event_date>=request.start)
                     and (not request.end or row.event_date<=request.end))

    def get_daily_bars(self, request):
        self.record('bars',request)
        rows=tuple(row for row in self.rows if (not request.start or row.trade_date>=request.start)
                   and (not request.end or row.trade_date<=request.end))
        if request.count:
            rows=rows[-request.count:]
        return BatchResult(tuple(Success(code,DailyBarSeries(rows,request.adjustment)) for code in request.codes))

    def get_reports(self, request):
        self.record('reports',request)
        if self.report_failure:
            return BatchResult(tuple(Failure(code,ItemError('SOURCE_ERROR','failed')) for code in request.codes))
        field='m_timetag' if request.date_basis=='report_time' else 'm_anntime'
        rows=tuple(row for row in self.balance if (not request.start or getattr(row,field)>=request.start)
                   and (not request.end or getattr(row,field)<=request.end))
        values={name:(rows if name=='Balance' else ()) if name in request.tables else None
                for name in ('Balance','Income','CashFlow','Capital','PershareIndex')}
        return BatchResult(tuple(Success(code,FinancialReports(**values)) for code in request.codes))

    def providers(self):
        return Providers(self,self,self,self,self,self,self,
                         Capabilities({op:Capability(True,'synthetic',None) for op in OPERATIONS}))


@pytest.fixture
def setup(tmp_path):
    source=Source()
    config=StorageConfig.from_config({},tmp_path)
    repository=Repository(config.path,config.source_scope)
    now=[NOW]
    data=PersistentData(source.providers(),repository,config.policies,clock=lambda:now[0],
                        evidence=Proven(),adjustment=SampledBigQmtAdjustment())
    return source,repository,data,now


def test_day_policy_uses_shanghai_midnight_not_24_hours():
    updated=datetime(2026,9,28,15,59,tzinfo=timezone.utc)
    assert Freshness('day').valid(updated,updated)
    assert not Freshness('day').valid(updated,updated+timedelta(minutes=2))
    assert not Freshness('forever').valid(updated,updated-timedelta(seconds=1))


@pytest.mark.parametrize('value',[True,0,-1,float('inf'),'bogus'])
def test_invalid_policy_is_rejected(value,tmp_path):
    with pytest.raises(ValueError):
        StorageConfig.from_config({'cache_policies':{'instruments':value}},tmp_path)


def test_snapshots_survive_restart_and_refresh_failure_keeps_old_data(setup):
    source,repo,data,now=setup
    request=CachedCodesRequest(('600000.SH',))
    assert data.details(request).require_all()['600000.SH'].name=='name'
    source.fail=True
    restarted=PersistentData(source.providers(),Repository(repo.path,repo.scope),data.policies,clock=lambda:now[0])
    assert restarted.details(request).items[0].status=='ok'
    assert restarted.details(replace(request,refresh=True)).items[0].status=='error'
    assert restarted.details(request).items[0].status=='ok'
    now[0]+=timedelta(days=1)
    assert restarted.details(request).items[0].status=='error'


def test_snapshot_replaces_collection_and_preserves_known_empty(setup):
    source,repo,data,now=setup
    assert data.underlyings(RefreshRequest())==('510050.SH',)
    source.list_option_underlyings=lambda request: ()
    assert data.underlyings(RefreshRequest(True))==()
    source.fail=True
    assert data.underlyings(RefreshRequest())==()


def test_business_tables_roundtrip_dates_and_nullable_fields(setup):
    _,repo,data,_=setup
    data.details(CachedCodesRequest(('600000.SH',)))
    with sqlite3.connect(repo.path) as db:
        row=db.execute('SELECT name,float_volume,created_date FROM instruments').fetchone()
        assert row[0]=='name' and row[1]==1.
        assert 'placeholder' in row[2]


def test_range_reuse_partial_hit_and_source_namespace(setup):
    source,repo,data,_=setup
    first=DailyBarsQuery(('600000.SH',),START,START+timedelta(days=1),fill_data=False)
    assert len(data.daily_bars(first).require_all()['600000.SH'].rows)==2
    source.calls.clear()
    query=replace(first,end=END)
    result=data.daily_bars(query).require_all()['600000.SH']
    assert len(result.rows)==5
    assert [(r.start,r.end) for name,r in source.calls if name=='bars']==[(START+timedelta(days=2),END)]
    source.fail=True
    assert len(data.daily_bars(query).require_all()['600000.SH'].rows)==5
    other=Repository(repo.path,'other-source')
    assert other.read(Partition('daily_bars','600000.SH'),START,END,Freshness('forever'),NOW).coverage==()


def test_write_failure_after_partial_hit_returns_complete_memory_result(setup,monkeypatch):
    source,repo,data,_=setup
    query=DailyBarsQuery(('600000.SH',),START,START+timedelta(days=1),fill_data=False)
    data.daily_bars(query).require_all()
    monkeypatch.setattr(repo,'write',lambda writes:False)
    result=data.daily_bars(replace(query,end=END)).require_all()['600000.SH']
    assert len(result.rows)==5
    assert repo.read(Partition('daily_bars','600000.SH'),START,END,Freshness('forever'),NOW).coverage==((START,START+timedelta(days=1)),)


def test_unproved_source_result_returns_but_never_claims_coverage(setup):
    source,repo,data,_=setup
    data.evidence=ConservativeEvidence()
    request=DailyBarsQuery(('600000.SH',),START,END,fill_data=False)
    assert len(data.daily_bars(request).require_all()['600000.SH'].rows)==5
    snapshot=repo.read(Partition('daily_bars','600000.SH'),START,END,Freshness('forever'),NOW)
    assert snapshot.coverage==()
    assert len(snapshot.rows)==5
    source.fail=True
    assert data.daily_bars(request).items[0].status=='error'


def test_refresh_deletes_retracted_rows_and_preserves_ttl_of_tails(setup):
    _,repo,_,_=setup
    p=Partition('daily_bars','600000.SH')
    old=NOW-timedelta(days=2)
    repo.write((Write(p,tuple(bar(START+timedelta(days=i)) for i in range(5)),START,END,True,old),))
    middle=START+timedelta(days=2)
    repo.write((Write(p,(),middle,middle,True,NOW),))
    snapshot=repo.read(p,START,END,Freshness('forever'),NOW)
    assert len(snapshot.rows)==4
    current=repo.read(p,START,END,Freshness('day'),NOW)
    assert current.coverage==((middle,middle),)


def test_financial_date_views_and_same_basis_other_ranges_are_invalidated(setup):
    source,repo,data,_=setup
    query=FinancialQuery(('600000.SH',),('Balance',),START,END)
    assert data.financials(query).items[0].status=='ok'
    p=Partition('Balance','600000.SH','report_time')
    assert repo.read(p,START,END,Freshness('forever'),NOW).coverage
    alternate=replace(query,date_basis='announce_time')
    data.financials(alternate).require_all()
    assert not repo.read(p,START,END,Freshness('forever'),NOW).coverage
    p=replace(p,basis='announce_time')
    later=END+timedelta(days=1)
    source.balance=(replace(source.balance[0],m_anntime=later),)
    data.financials(replace(alternate,start=later,end=later)).require_all()
    snapshot=repo.read(p,START,later,Freshness('forever'),NOW)
    assert snapshot.coverage==((later,later),)
    assert not any(a<=START<=b for a,b in snapshot.coverage)


def test_refresh_adjustment_failure_does_not_publish_partial_dependencies(setup):
    source,repo,data,_=setup
    event=DividendEvent(END,datetime(2026,9,25,tzinfo=timezone.utc),.9,1.,0.,0.,0.,0.,0.)
    source.events=(event,)
    query=DailyBarsQuery(('600000.SH',),START,END,adjustment='front',fill_data=False)
    first=data.daily_bars(query).require_all()['600000.SH']
    assert first.rows[0].close==9.
    source.events=(replace(event,interest=2.),)
    original=source.get_daily_bars
    source.get_daily_bars=lambda r: BatchResult((Failure(r.codes[0],ItemError('SOURCE_ERROR','failed')),))
    assert data.daily_bars(replace(query,refresh=True)).items[0].status=='error'
    snapshot=repo.read(Partition('dividend_events','600000.SH'),date.min,NOW.date(),Freshness('forever'),NOW)
    assert snapshot.rows[0].interest==1.
    source.get_daily_bars=original
    refreshed=data.daily_bars(replace(query,refresh=True)).require_all()['600000.SH']
    assert refreshed.rows[0].close==8.
    assert len([r for name,r in source.calls if name=='events'])==3


def test_same_day_events_reused_but_next_day_failure_rejects_adjustment(setup):
    source,_,data,now=setup
    query=DailyBarsQuery(('600000.SH',),START,END,adjustment='front',fill_data=False)
    data.daily_bars(query).require_all()
    source.fail=True
    data.daily_bars(query).require_all()
    now[0]+=timedelta(days=1)
    assert data.daily_bars(query).items[0].status=='error'
    assert data.daily_bars(replace(query,adjustment='none')).items[0].status=='ok'


def test_today_bars_are_never_persisted(setup):
    source,repo,data,_=setup
    today=NOW.astimezone(SHANGHAI).date()
    source.rows+=(bar(today),)
    request=DailyBarsQuery(('600000.SH',),START,today,fill_data=False)
    assert len(data.daily_bars(request).require_all()['600000.SH'].rows)==6
    rows=repo.read(Partition('daily_bars','600000.SH'),START,today,Freshness('forever'),NOW).rows
    assert all(row.trade_date<today for row in rows)


def test_concurrent_snapshot_misses_are_coalesced(setup):
    source,_,data,_=setup
    original=source.get_details
    def slow(request):
        time.sleep(.02)
        return original(request)
    source.get_details=slow
    request=CachedCodesRequest(('600000.SH',))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:data.details(request),range(8)))
    assert all(result.items[0].status=='ok' for result in results)
    assert len(source.calls)==1
    assert not data.locks._entries


def test_corrupt_database_is_preserved_and_source_still_answers(tmp_path):
    path=tmp_path/'broken.sqlite';path.write_bytes(b'not a database')
    repo=Repository(path,'source')
    source=Source();policies=StorageConfig.from_config({},tmp_path).policies
    data=PersistentData(source.providers(),repo,policies,clock=lambda:NOW)
    assert data.details(CachedCodesRequest(('600000.SH',))).items[0].status=='ok'
    assert path.read_bytes()==b'not a database'
    assert repo.health()['degraded']
    source.fail=True
    assert data.details(CachedCodesRequest(('600000.SH',))).items[0].status=='error'


def test_transaction_failure_rolls_back_rows_and_coverage(setup):
    _,repo,_,_=setup
    p=Partition('daily_bars','600000.SH')
    assert repo.write((Write(p,(bar(START),),START,END,True,NOW),))
    good=Write(p,(bar(START,20.),),START,END,True,NOW)
    bad=Write(p,(bar(END+timedelta(days=1)),),START,END,True,NOW)
    assert not repo.write((good,bad))
    assert repo.read(p,START,END,Freshness('forever'),NOW).rows[0].close==10.


def test_count_does_not_treat_first_cached_row_as_history_start(setup):
    source,_,data,_=setup
    data.daily_bars(DailyBarsQuery(('600000.SH',),START,END,fill_data=False)).require_all()
    source.calls.clear()
    data.daily_bars(DailyBarsQuery(('600000.SH',),end=END,count=10,fill_data=False)).require_all()
    assert any(name=='bars' for name,_ in source.calls)


def test_default_fill_and_batch_order(setup):
    _,_,data,_=setup
    result=data.daily_bars(DailyBarsQuery(('B.SH','A.SH'),START,END))
    assert tuple(item.code for item in result.items)==('B.SH','A.SH')
    assert all(len(item.value.rows)==5 for item in result.items)


def test_unknown_schema_is_not_replaced(tmp_path):
    path=tmp_path/'future.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=999')
    repo=Repository(path,'source')
    assert repo.health()['degraded']
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==999


def test_count_reuses_confirmed_suffix_without_claiming_history_start(setup):
    source,_,data,_=setup
    data.daily_bars(DailyBarsQuery(('600000.SH',),START,END,fill_data=False)).require_all()
    source.fail=True
    result=data.daily_bars(DailyBarsQuery(('600000.SH',),end=END,count=2,fill_data=False)).require_all()
    assert tuple(row.trade_date for row in result['600000.SH'].rows)==(END-timedelta(days=1),END)


def test_numeric_ttl_changes_apply_without_rewriting_rows(setup):
    _,repo,data,now=setup
    p=Partition('instruments','600000.SH')
    data.details(CachedCodesRequest(('600000.SH',))).require_all()
    later=NOW+timedelta(hours=2)
    assert repo.read(p,date.min,date.max,Freshness('ttl',3600),later,'complete').state_updated is None
    assert repo.read(p,date.min,date.max,Freshness('ttl',10800),later,'complete').state_updated==NOW


def test_invalid_refresh_rejected_and_not_added_to_live_requests():
    from qmt_rpyc.contracts.common import CodesRequest
    from qmt_rpyc.transport.codec import decode
    with pytest.raises(ValueError):
        CachedCodesRequest(('600000.SH',),refresh=1)
    with pytest.raises(Exception):
        decode(CodesRequest,{'codes':['600000.SH'],'refresh':True})
    for op in ('reference.list_sectors','reference.get_sector_members','downloads.start_sectors'):
        assert op not in OPERATIONS


def test_store_failure_after_source_success_reports_degraded_health(setup,monkeypatch):
    _,repo,data,_=setup
    def fail(*args):
        raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(repo,'_write',fail)
    assert data.details(CachedCodesRequest(('600000.SH',))).items[0].status=='ok'
    assert repo.health()['write_failures']==1
    assert repo.health()['degraded']


def test_batch_failure_is_not_persisted_as_empty_success(setup):
    source,repo,data,_=setup
    def load(request):
        return BatchResult(tuple(Failure(code,ItemError('NOT_FOUND','absent')) if code=='BAD'
                                 else Success(code,instrument()) for code in request.codes))
    source.get_details=load
    result=data.details(CachedCodesRequest(('BAD','GOOD')))
    assert result.items[0].error.error_type=='NOT_FOUND'
    assert result.items[1].value.name=='name'
    snapshot=repo.read(Partition('instruments','BAD'),date.min,date.max,Freshness('day'),NOW,'complete')
    assert snapshot.state_updated is None


def test_dispatch_cache_hit_survives_source_capability_unavailable(setup):
    from qmt_rpyc.server.dispatch import Dispatcher
    from qmt_rpyc.transport.codec import dumps,loads
    from qmt_rpyc.contracts.operations import CONTRACT_VERSION
    source,repo,data,_=setup
    data.details(CachedCodesRequest(('600000.SH',))).require_all()
    original=source.providers()
    caps=dict(original.capabilities.operations)
    caps['instruments.get_details']=Capability(False,None,'source missing')
    wrapped=decorate(replace(original,capabilities=Capabilities(caps)),repo,data.policies,clock=lambda:NOW)
    def unavailable(request):
        raise AssertionError("unexpected trading call")
    trading=SimpleNamespace(**{name:unavailable for name in ("get_asset","list_positions","list_orders","submit_order","cancel_order")})
    dispatcher=Dispatcher(replace(wrapped,trading=trading))
    request=dict(contract_version=CONTRACT_VERSION,request_id='storage-test',operation='instruments.get_details',
                 payload={'codes':['600000.SH']})
    assert loads(dispatcher.call(dumps(request)))['data']['items'][0]['status']=='ok'
    request['payload']['refresh']=True
    assert loads(dispatcher.call(dumps(request)))['data']['items'][0]['status']=='error'


def test_refresh_financial_failure_preserves_both_rows_and_coverage(setup):
    source,repo,data,_=setup
    request=FinancialQuery(('600000.SH',),('Balance',),START,END)
    first=data.financials(request).require_all()
    source.report_failure=True
    assert data.financials(replace(request,refresh=True)).items[0].status=='error'
    assert data.financials(request).require_all()==first


def test_adjustment_ratio_direction_and_multiple_affine_events():
    events=(DividendEvent(START+timedelta(days=1),NOW,1.25,2.,0.,0.,0.,0.,0.),
            DividendEvent(END,NOW+timedelta(seconds=1),2.,0.,1.,0.,0.,0.,0.))
    policy=SampledBigQmtAdjustment()
    before=bar(START,10.)
    assert policy.derive((before,),events,'front')[0].close==4.
    assert policy.derive((before,),events,'front_ratio')[0].close==4.
    after=bar(END,4.)
    assert policy.derive((after,),events,'back')[0].close==10.
    assert policy.derive((after,),events,'back_ratio')[0].close==10.


def test_future_calendar_rejected_even_with_cached_data(setup):
    _,_,data,_=setup
    with pytest.raises(ProviderError):
        data.trading_dates(TradingDatesRequest('SH',end=NOW.date()+timedelta(days=1)))


def test_unverified_suspension_fill_uses_exact_source_request(setup):
    source,_,data,_=setup
    source.rows=(bar(START),bar(END))
    source.get_trading_dates=lambda request: tuple(START+timedelta(days=i) for i in range(5))
    request=DailyBarsQuery(('600000.SH',),START,END,fill_data=True)
    data.daily_bars(request).require_all()
    assert source.calls[-1]==('bars',request)


def test_observed_rows_do_not_leak_into_partial_hit_merge(setup):
    source,repo,data,_=setup
    middle=START+timedelta(days=2)
    p=Partition('daily_bars','600000.SH')
    repo.write((Write(p,(bar(START),),START,START,True,NOW),
                Write(p,(bar(middle,999.),),middle,middle,False,NOW)))
    source.rows=(bar(START),bar(END))
    result=data.daily_bars(DailyBarsQuery(('600000.SH',),START,END,fill_data=False)).require_all()['600000.SH']
    assert tuple(row.trade_date for row in result.rows)==(START,END)


def test_detail_cache_preserves_grouped_source_reads_and_only_fetches_misses(setup):
    source,_,data,_=setup
    data.details(CachedCodesRequest(('A','B'))).require_all()
    assert len(source.calls)==1 and source.calls[0][1].codes==('A','B')
    source.calls.clear()
    result=data.details(CachedCodesRequest(('C','B','A')))
    assert source.calls[0][1].codes==('C',)
    assert tuple(item.code for item in result.items)==('C','B','A')
