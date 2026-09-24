# 固定 V1 契约的使用方依赖

2026-09-23 只读检查：a-trader `2c40007`，a-options `7d0fad7`，检查时两仓库工作区均干净。未调用 RPC、读取账户数据或执行 live 测试。下文记录代码依赖，不能替代 Windows SDK 的实际行为验证。

## 必需接口并集

共 14 个 SDK 接口：10 个 xtdata、4 个 Trader。所有名称及当前调用参数都能与 [实机清单](qmt-api-baseline-20260923.json) 中的签名对应。A 表示 a-trader，O 表示 a-options。

| 命名空间 | API | 使用方 | 实际调用约束 |
| --- | --- | --- | --- |
| xtdata | `get_full_tick` | A、O | 股票/ETF/期权代码列表 |
| xtdata | `get_trading_dates` | A | 市场、起止日期；省略 count |
| xtdata | `download_history_data` | A、O | 代码、周期、起止日期；桥接返回下载任务 |
| xtdata | `get_market_data_ex` | A、O | `field_list=[]`、代码列表、周期、起止日期；依赖 SDK 默认的未复权行为 |
| xtdata | `get_divid_factors` | A、O | 代码、起止日期 |
| xtdata | `get_stock_list_in_sector` | A | 板块名称 |
| xtdata | `get_instrument_detail` | A、O | `iscomplete=True`；A 还使用 batch |
| xtdata | `get_option_undl_data` | O | `None` 查询标的集合，或指定标的查询合约集合 |
| xtdata | `get_option_list` | O | 标的、到期日期、空 opttype |
| xtdata | `get_option_detail_data` | O | 单合约查询和 batch 均有运行时调用 |
| trader | `query_stock_asset` | A | account ID 字符串 |
| trader | `query_stock_orders` | A | account ID 字符串，默认 `cancelable_only=False` |
| trader | `query_stock_positions` | A | account ID 字符串；用于券商可卖数量检查 |
| trader | `order_stock` | A | account、code、买卖枚举、数量、报价枚举、价格、strategy、remark |

来源：`a-trader/core/market_data/qmt.py:25`、`core/execution/qmt.py:52`、`core/runtime/qmt.py:230`；`a-options/market/qmt_gateway.py:98`。a-trader 的 `_REQUIRED_TRADER` 漏掉了实际使用的 `query_stock_positions`，因此以上清单基于实际调用，而不是单独依赖启动检查名单。

## 用户新增的撤单需求

用户在依赖调查后明确要求补入撤单。另加入 `trader.cancel_order_stock(account, order_id)` 和 `trader.cancel_order_stock_sysid(account, market, sysid)` 两个同步接口，基础能力范围为原 14 个依赖接口加 2 个撤单接口；不能把新增需求误记成两个使用方已有调用。

同步扩充委托查询的固定字段，至少加入 `order_sysid`、`status_msg` 及账户和交易方向等核对信息。`order_sysid` 的字段定义已由 [SDK 源码证据](contract-v1-sources.json) 中的 `XtOrder` 确认；实际类型及空值仍需验证。补充 `SH_MARKET=0`、`SZ_MARKET=1`，以及 `ORDER_UNREPORTED=48`、`ORDER_WAIT_REPORTING=49`、`ORDER_REPORTED=50`、`ORDER_UNKNOWN=255`，与现有常量一起表达委托全生命周期，共 16 个选定常量。所有值均来自本次部署清单。

撤单后通过已有 `query_stock_orders` 查询状态与实际成交数量。SDK 报告撤单调用成功不被包装成“该委托必然完全未成交”；超时或响应无法确认时报告结果不确定，不自动重发撤单。两个异步撤单变体存在于 SDK，但本轮不导出。

## 返回字段最低需求

这是使用方字段需求的下限，不是已验证的最终 schema。类型、单位、缺失与空值规则还需 SDK 定义或样本支持。可以为股票分析加入有明确用途、经过验证的字段，但不能动态暴露所有 SDK 属性。

| 数据 | 最低所需字段/结构 | 证据 |
| --- | --- | --- |
| Tick | 按证券代码映射；`lastPrice, volume, time, bidPrice, askPrice, lastSettlementPrice, openInt`，盘口价格为数组 | A `core/market_data/provider.py`；O `core/market_data/_chain.py:149`、`core/portfolio/_valuation.py:184` |
| 交易日 | 毫秒时间戳列表；空列表允许非交易日，不能把异常变成空列表 | A `core/runtime/qmt.py:247`、`:361` |
| K 线 | 按证券代码映射 split 表 `{columns, index, data}`；所需列 `time/open/high/low/close/volume/amount`，时间也可能取自 index | A `core/market_data/qmt.py:82`；O `market/qmt_gateway.py:209` |
| 除权除息 | A 使用 `time/dr/interest`；O 期望日期到 factor 的映射或 time/factor 列数组，二者语义和结构尚未统一 | A `core/market_data/dividend.py:109`；O `core/market_data/_chain.py:284` |
| 标的/合约资料 | `InstrumentName, InstrumentID, UniCode`；期权辅助还依赖 `OptUndlCode, OptExercisePrice, OptionType, ExpireDate, OptUnit` | A `core/dashboard/routers/market_data.py:95`、`core/market_data/option.py:41`；O `market/qmt_gateway.py:127` |
| 期权详情 | `ExpireDate, OptExercisePrice, optType, OptUnit, InstrumentName`；optType 为 CALL/PUT | O `core/market_data/_chain.py:164` |
| 资产 | `account_id, cash, frozen_cash, market_value, total_asset` | A `core/execution/qmt.py:52` |
| 委托 | `order_status, order_id, order_time, order_volume, traded_volume, traded_price, stock_code, order_remark` | A `core/execution/qmt.py:70` |
| 持仓 | `stock_code, volume, can_use_volume` | A `core/execution/qmt.py:137` |
| 下单结果 | 成功委托 ID；明确拒绝与执行结果不确定须分开，不能把响应校验失败转成负数或 None | A `core/execution/venue.py:208`、`core/runtime/qmt.py:334` |

`field_list=[]` 在固定契约中应表示该数据模型约定的字段全集，而不是未来 SDK 新增字段的全集。股票代码、日期等动态键属于业务数据，不能按静态对象字段名单裁剪。证券代码后缀需要保留并验证；O 当前接受期权 `.SHO/.SZO` 与股票/ETF `.SH/.SZ`。

时间和数值不能从宽松解析器反推真实语义：A 接受多种时间格式并将日线归到上海时区零点，O 某些日线逻辑依赖 YYYYMMDD 字符串。A 的研究路径允许传入不同 period，不能只因 O 使用日线便把契约限制为 `1d`。A 还存在负成交量的有符号 int32 修复，考虑股/手两种倍率；当前没有证据把所有品种的 volume 统一声明为股、手或张，也不能直接把该启发式迁入服务端作为通用保证。

后续实测已取得[返回模型对照](contract-v1-returns.md)：默认日线/1m 的 split 表没有 time 列，index 分别是八位/十四位整数；日线显式请求 time 列时该列是毫秒整数。A 的实际默认生产路径为 1d，但研究 HTTP API 未限制 period 字符串；周/月/60m 本次 RPC 报 TypeError，不能将任意可输入字符串当成已验证能力。另确认期权详情缺名称、完整资料的 OptUndlCode 等在 ExtendInfo；A 的旧顶层读取须在迁移中对齐。

Q7 补查发现 A 的两条路径严格依赖不同单位：`core/execution/qmt.py:91` 将 `order_time` 转为正整数但不换单位；`core/execution/reconciliation.py:492` 直接按 Unix 秒解析并核验委托日期，`:394` 的人工终态证据也明确声明秒。`core/runtime/qmt.py:363` 则将交易日列表每项除以 1000 后取日期。直接全局改成毫秒会使前者对账失败，改成秒或日期字符串则破坏后者。O 的 `market/qmt_gateway.py:238` 将日线 index 转字符串，`core/market_data/_chain.py:306` 再取前八位参与复权日期比较。这些是消费约束，并非原生返回单位已获验证的证据。

## 常量与桥接能力

A 启动要求 10 个常量：`STOCK_BUY=23`、`STOCK_SELL=24`、`LATEST_PRICE=5`、`ORDER_REPORTED_CANCEL=51`、`ORDER_PARTSUCC_CANCEL=52`、`ORDER_PART_CANCEL=53`、`ORDER_CANCELED=54`、`ORDER_PART_SUCC=55`、`ORDER_SUCCEEDED=56`、`ORDER_JUNK=57`。值来自本次部署快照。订单状态字段和常量应成对适配；不能对全部整数作无上下文替换，也不能把未知状态映射成成功或拒绝。

两个仓库都依赖 `QmtClient.connect`。A 还依赖 `close()`、私有 `_surface` 预检和 `health()` 的 `connected/trader_available/last_heartbeat/heartbeat_failures/reconnect_attempts/uptime_seconds`；引入固定契约时应提供公开的契约/能力查询并迁移该预检。

batch 用于合约详情，消费方每组 100，依赖顺序不变及逐项 `{status, data}` 或 `{status, error_message}`。保留现有最多 500 项、仅同一 xtdata 函数、有界并发的桥接规则。

A 依赖 `DownloadTaskHandle.wait()` 返回任务状态及错误。O 当前忽略下载返回，必须在迁移中明确等待再读取。没有发现两个仓库直接调用 SDK 回调、行情订阅、事件轮询、Trader 生命周期控制或自检；这些不构成本次必需导出范围。

## 一次迁移必须处理的问题

1. 两仓库 requirements 和启动安装器都锁定 `qmt-rpyc==0.3.1rc1`。须同步更新 `a-trader/requirements.txt:11`、`core/runtime/qmt_upgrade.py:13`、`a-options/requirements.txt:7`、`core/upgrade.py:19`，否则手工装的新版本可能在启动时被换回旧版。
2. O 在 `market/qmt_gateway.py:267` 下载后直接读取 K 线，存在任务尚未完成的竞态；不能让桥接层把“已入队”冒充“下载完成”。
3. O 的分红因子解析无法处理 split 表，可能静默得到空因子集。其接口文档称 cumulative factor，而实现逐事件累乘；A 使用 dr 和 interest。必须先验证字段含义，再确定消费者转换，不能把猜测固化成契约。
4. A 的 `core/market_data/option.py:16` 记录部署 SDK 因 `OptUndlUniCode=None` 导致期权函数失败，并已改用板块列表与完整合约资料组合实现；O 仍直接依赖这些函数。该记录说明需要真实适配器，不是当前 Windows 环境必然仍有同一故障的证明。应验证后将适用的兼容逻辑放在服务端，使两者共享同一结果约定。
5. A 的资产字段缺失可能被填成 0；O 多处吞异常返回空集合，未知 optType 可能被当 PUT、缺失 OptUnit 被当 10000。固定契约必须明确报错，迁移时让契约错误可见，不能将这些宽松回退当作正确行为。
6. A 的 submit_once 不重试下单，需继续保持。执行前契约拒绝可以明确未执行；调用已进入 SDK 后无法确认结果应报结果不确定，不能返回会被消费者解释为拒单的 None/-1。
7. A 的连接流程主动探测账户和交易日，因此“RPC 仍可连接”和“a-trader 所需能力已满足”是不同状态。新增分析接口不可用不能导致全局握手失败；必需交易接口不可用时应用应明确禁用受影响操作。
8. 任何时间单位调整都必须覆盖委托对账、人工终态证据、交易日解析与日线复权日期比较；仅修改服务器通用序列化器不能完成迁移。V1 可逐字段固定现有格式，避免为统一单位而额外改变这些行为。

上述问题仅记录为契约迁移范围，本轮没有修改两个使用方或其业务计算。
