# V1 返回模型草案与实测对照

2026-09-23。本文细化已确认的 Q4、Q6、Q7：只返回约定字段，保留现有容器和字段名，逐字段固定现有时间格式。本页保留取证与候选分析；已选择字段的运行时定义见 [V1 快照](../../src/qmt_rpyc/contracts/v1.json)，验收边界见[实现记录](../design/contract-v1-implementation.md)。样本提供实际返回证据，不能单独证明所有品种、参数、单位或空值情况。

## 证据及采集范围

- 用户提交的 [Windows 本机采集文件](contract-v1-samples-20260923.json) 仅有 `collection_status=importing` 和 `calls=[]`；用户确认命令已返回 PowerShell。该文件未取得业务响应，也不足以确定进程退出原因。原文件保留，SHA-256 为 `d67b6db0ac3ab23ee09e5ad4ce6e51c2655eca3b6897814cde90d81ff8c1366b`。
- 根据用户指示将本地客户端从 `0.3.1rc1` 升至 PyPI 当时最新正式版 `0.3.1`，使用既有连接配置只读检查；服务端报告 `0.3.1`、传输协议和接口清单格式均为 `1`。
- [RPC 样本](contract-v1-rpc-samples-20260923.json) 在 `2026-09-23T18:31:28Z` 至 `18:31:31Z` 采集，21 次调用全部正常返回，覆盖 12 个只读 xtdata API。所采 API 的公开签名与部署基线一致。SHA-256 为 `c766673b51f93b629da905698166efee22e74a76d4c0a8c3572f833a0e2a56c2`。
- [周期与字段参数补采](contract-v1-rpc-periods-20260923.json) 另执行 11 次只读 K 线查询，7 次正常返回，4 次返回远端 TypeError；用于区分接口名称可用与参数分支可用。SHA-256 为 `1a3ef3e42ed6a15aa3db21219d90c40977727cdcce930929111ef43612347958`。
- 这些是客户端实际收到的序列化结果，不包含原生 numpy dtype。每张表保留全部列和至多三行；普通长列表保留十项并记录总数；指数权重的全部 300 项完整保留。每次调用的参数、成功/失败和截断位置均记录在文件中。
- 没有执行下载、订阅、账户查询、下单或撤单。当前 RPC 可用是本次取证条件，不改变启动修复不能依赖旧 RPC 的约束。

以下调用编号从 1 开始。`number` 表示有限数值，兼容 int/float，但不包括 bool；`integer` 表示整数，同样不包括 bool。价格 `3` 和 `3.0` 都是合法数值，不能将 JSON 数字的表示差异当作 SDK 不兼容。

## 行情与交易日

`get_full_tick` 保留 `{证券代码: Tick}`。调用 1、21 中股票、ETF 和一份期权的 Tick 都有以下 18 个字段，可作为 V1 白名单候选：

| 字段 | 观测格式与约束 |
| --- | --- |
| `time` | integer，Unix 毫秒；与 `timetag` 按 UTC+8 解释相符 |
| `timetag` | string，观测到 `YYYYMMDD HH:MM:SS` 和带三位小数的形式；若导出，明确允许这两种表示 |
| `lastPrice, open, high, low, lastClose, amount, settlementPrice, lastSettlementPrice` | number；0 的业务意义逐字段保留，不将所有 0 当成缺失 |
| `volume, pvolume, stockStatus, openInt` | 样本为 integer；状态码及数量单位不凭数值大小猜测 |
| `askPrice, bidPrice` | number 数组；样本为五档，尚不足以证明所有品种必须恰好五档 |
| `askVol, bidVol` | integer 数组；按原次序保留 |

股票样本中 `pvolume` 约为 `volume` 的 100 倍，期权两者相等。此证据要求按品种核验数量含义，不支持全局乘除 100。有效字段的空值规则仍应由相应品种模型明确，不能因本次样本非空而假设所有情形非空。

`get_trading_dates` 保留 `list[integer]`。调用 2、3 分别为 SH、SZ；三项毫秒时间戳对应上海时区的 `2026-09-21/22/23 00:00:00`。V1 固定该编码，不改成日期字符串。合法无交易日允许空列表；查询失败不能转为空列表。

## K 线

Q10 已确认首版周期范围为 `1m/5m/15m/30m/1h/1d`。用户说明当前部署 SDK 不支持周线、月线，需要自行计算；桥接层不提供周/月聚合，不将 `60m` 当成 `1h` 别名。查询及对应历史下载的范围外周期应在调用前报错。

`get_market_data_ex` 保留 `{证券代码: {columns, index, data}}`，三者均为列表。索引与行数一致，每行宽度与 columns 一致。默认列的顺序已在调用 4、5 取得：

```text
open, high, low, close, volume, amount,
settelementPrice, openInterest, preClose, suspendFlag
```

`settelementPrice` 是部署实际拼写；若纳入契约就保持此拼写。默认结果没有 `time` 列，a-trader 会取 index，不能将未返回的 time 列列为默认必需字段。`field_list=[]` 可固定为以上十列。补采调用 9 对日线显式请求 `[time, open, close, volume]`，返回列与请求顺序一致：其中 time 列是毫秒整数，index 仍为八位日期整数。字段白名单可覆盖默认十列与显式 time，默认列集合和可请求集合分别定义。

| 位置 | 实测格式 | V1 约束 |
| --- | --- | --- |
| `period=1d` 的 index | integer，例如 `20260918` | 八位交易日期，不转成 Unix 时间戳 |
| `period=1m` 的 index | integer，例如 `20260918145800` | 十四位本地市场时间，不按秒或毫秒解释 |
| `period=1d` 显式 time 列 | integer，例如 `1789660800000` | Unix 毫秒，对应同一行的 `20260918`；同一张表的 index 与 time 列不能应用同一数字转换规则 |
| OHLC、preClose | number 或 null | 允许已观测的缺失价格，不补成 0，不静默删除对应行 |
| volume、openInterest、suspendFlag | 样本为 integer | 保持原值；状态语义和跨品种单位另行核验 |
| amount、settelementPrice | 样本为 number | 保持原值；不因整数或浮点表示不同拒绝响应 |

调用 5 中部分股票/ETF 的 OHLC 和 preClose 为 null，同时 volume 为 0、suspendFlag 为 1；调用 4 中也有价格重复、volume 为 0 的行。必须区分“列存在但数据缺失”和“非空表缺少契约必需列”。不从这些样本自行判断缺失的原因。

本次查询 end_time 到 9 月23日，返回最后一根 K 线为 9 月18日，而 tick 为 9 月23日。查询正常返回不证明数据覆盖到请求结束时间；保留 Q9 和使用方的数据覆盖检查，不自动下载或制造补齐数据。

周期补采的 `5m/15m/30m/1h` 均返回有上述十列的空表，说明这些请求正常结束，但没有证明非空 index 的格式；`60m/1w/1wk/1mon` 本次均返回 TypeError，不能据接口名称存在就承诺这些参数可用，也不能据本次错误断言所有 SDK 部署都不支持。选择上市前日期的日线返回有列名的空表。空表允许保留列名；财务另外观察到没有列名的空表，两种分支均应明确描述。

后续只读复查 60m/1w/1mon（同一标的、空 field_list、count=1）得到的错误首行均为 `[TypeError] 'NoneType' object is not iterable`。源码中这三个值进入 v3 返回路径，上层对结果直接迭代；单凭错误不能确定具体根因。用户随后明确补充当前 SDK 不支持周/月线，并选择不将其纳入首版；该事实来源与 RPC 错误证据分开记录。原样本文件未修改，Q10 决策见[设计方案](../design/stable-api-contract.md)。

a-trader 自带研究 UI、当前策略、监控和回测准备固定或默认使用 1d；研究 HTTP API 的 period 是未限制枚举的字符串，会原样下传。按 Q10 对齐 V1 的六类周期并同步校验；本地 mocked 周/月线测试不构成保留桥接周/月能力的要求。

## 除权除息

`get_divid_factors` 保留单个 split 表。调用 6、8、10 的列一致：

```text
time, interest, stockBonus, stockGift, allotNum, allotPrice, gugai, dr
```

index 是八位日期字符串；time 是浮点数形式的 Unix 毫秒，例如 `1752595200000.0` 对应 index `20250716` 的上海零点。按 Q7 保留这种格式，不因其数值是整毫秒就擅自变更字段类型。其余七列观测为数值。

该证据确认 a-options 现有日期→factor/两列数组解析器与真实 split 表不匹配。迁移时必须读取列名与行值；不能为了适配旧解析器，把 dr 直接改名 factor 或默认认定为累计复权倍率。单次/累计、分配基准和特殊事件含义仍需定向验证，不通过实际交易取证。

## 合约与期权

`get_instrument_detail(iscomplete=True)` 保留带嵌套 `ExtendInfo` 的字典。调用 7、9、11、20 中：

- `ExchangeID, InstrumentID, InstrumentName` 均为字符串；未观察到 `UniCode`，不能将它设为原生必需字段。
- `CreateDate, OpenDate, ExpireDate` 是字符串，但允许已观测的占位值 `"0"`、`"99999999"`；不能统一用合法日历日期校验。
- `PreClose, SettlementPrice, UpStopPrice, DownStopPrice, PriceTick, FloatVolume, TotalVolume` 为数值；`VolumeMultiple` 为整数，可作为股票分析字段候选。
- `IsTrading` 是 bool；本次为凌晨采集，不能将 false 解释为该证券永久不可交易。
- 期权的 `OptUnit, OptExercisePrice, OptUndlCode, OptUndlMarket` 位于 ExtendInfo。只投影约定的扩展字段，不把不明的限量值、配额、占位属性全量导出。

`get_option_detail_data` 保留扁平字典。调用 19 的核心字段：

| 字段 | 观测 |
| --- | --- |
| `ExpireDate, OpenDate, CreateDate, EndDelivDate` | 八位日期字符串 |
| `OptExercisePrice` | number，样本为 3.2 |
| `OptUnit` | number，样本为 10000.0；不能根据 mock 认定原生是 int |
| `OptUndlCode, OptUndlMarket` | 字符串 `510050`、`SH` |
| `optType` | `PUT`；源码支持 CALL/PUT，未知不能默认为 PUT |
| `InstrumentName` | SDK 详情缺失，但调用 20 的完整资料有值 |

若 V1 为使用方提供 InstrumentName，服务端明确从完整资料补入，并对依赖关系做兼容检查。a-trader 旧期权绕过逻辑读取完整资料的顶层 OptUndlCode，本次实际值在 ExtendInfo；一次迁移应将该读取对齐固定模型，或统一调用桥接期权方法，不能把旧故障记录当成当前必须继续绕过的证明。

调用 16 的 `get_option_list` 返回 `list[str]`；调用 17 的 `get_option_undl_data("510050.SH")` 返回 `list[str]`；调用 18 的 `get_option_undl_data(None)` 返回 `dict[str, list[str]]`。动态证券键和代码后缀保留。None 模式还观察到历史 CFFEX 标的，不能把桥接层所有证券标识限制成仅六位 SH/SZ 股票。

此处采到的是一份已到期沪市 PUT 合约，不能据此宣布所有有效/调整/CALL/深市期权均已验证。Q8 的六位月份、八位日期及 isavailavle 语义继续按部署源码保留。

## 财务和指数分析

`get_financial_data` 保留 `{证券代码: {表名: split表}}`。调用 14 请求当前八表，600000.SH 八表均有数据；000001.SZ 仅 Income/Capital 有数据，另六表返回 `{"columns":[],"index":[],"data":[]}`。

**空表是合法分支**：允许三个空列表；非空表则验证约定列及每行类型。列存在但值为 null 与列缺失分开处理，不能为未知值填 0。财务 index 为行号，报告/公告日期位于 m_timetag/m_anntime 或 endDate/declareDate，是八位字符串。

建议先选以下有分析用途、已观察到列名和非空类型的字段，不因本次拿到全部列就全部导出。数值列允许 null，日期列保留各自名称；未明确的单位与累计/单期含义必须在发布前核验。

| 表 | 字段白名单候选 |
| --- | --- |
| Balance | m_timetag、m_anntime、tot_assets、tot_liab、tot_shrhldr_eqy_excl_min_int、total_equity、cap_stk |
| Income | m_timetag、m_anntime、revenue、oper_profit、net_profit_excl_min_int_inc、s_fa_eps_basic、s_fa_eps_diluted |
| CashFlow | m_timetag、m_anntime、net_cash_flows_oper_act、net_cash_flows_inv_act、net_cash_flows_fnc_act、cash_cash_equ_end_period |
| Capital | m_timetag、m_anntime、total_capital、circulating_capital、restrict_circulating_capital、freeFloatCapital |
| HolderNum | declareDate、endDate、shareholder、shareholderA、shareholderB、shareholderH、shareholderFloat、shareholderOther |
| Top10Holder / Top10FlowHolder | declareDate、endDate、quantity、ratio、rank |
| PershareIndex | m_timetag、m_anntime、s_fa_bps、s_fa_ocfps、s_fa_eps_basic、s_fa_eps_diluted、du_return_on_equity、gear_ratio |

未将 name/type/reason/nature 列纳入上述白名单：本次这些列均为 null，缺少其非空类型证据。也不暴露所有 m_ 元数据列。Income.revenue 有值而 operating_revenue 为 0，不能按近似英文名称互换；Capital.m_quarter 出现巨大数值，不能推断为 1–4 的季度编号。

八个查询表名已有证据，空 table_list 的固定集合可据此定义；下载默认另含 PerShare 的差异仍需在实现规格中明确处理。财务补充能力的语义验收不得阻塞原十四个依赖接口与两个撤单接口的迁移。

`get_index_weight` 的调用 15 返回完整 `{成分代码: number}`，300 项和为 100.0。这支持按代码映射数值的模型；百分数单位是强推断，尚不足以单凭一个样本证明更新日、完整性与所有指数口径。保留数值，不偷偷除以 100；接口未提供 as-of，不添加虚构日期。

`get_sector_list`、`get_stock_list_in_sector` 保留 `list[str]`；合法空列表与失败分开。

## 未由本次样本验证的范围

- 三个 Trader 查询：已取得类型定义与使用方字段需求，但本次未读账户数据。order_time 的秒约定来自 a-trader 对账依赖，原生响应与异常/空值仍需定向验证。
- 下单、两个撤单：使用已取得的源码定义和模拟响应校验，不通过真实交易采样；执行前拒绝与进入 SDK 后结果不确定继续分开表达。
- 四个下载：按 Q9 返回任务句柄；completed 表示 SDK 调用正常结束，实际 SDK 返回 None 不被当作失败。
- 5m/15m/30m/1h 的非空返回、分钟线显式 time 列等尚未取得非空证据；不可把日线的结果推广成所有分支。周/月/60m 已按 Q10 排除在首版范围之外，不再列为本轮的兼容适配待办。
- 复权因子、数量、财务及指数权重的业务单位不从字段拼写或少量样本臆定。返回模型与运行时投影可以据现有证据落实，语义兼容保证仍需针对性验证。

## 复权补采

实施期间新增[只读对照](contract-v1-rpc-dividends-20260923.json)：510050.SH 与 600000.SH 的五次现金分红、1,311 根有效日线，原始 close / front_ratio close 与后续 dr 的乘积误差不超过 2.22e-16。因此 a-options 可以提取原字段 dr，沿用已有逐事件乘积的除法复权；不据此宣称所有特殊公司行为都已验证。
