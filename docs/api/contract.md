# 公共 API 与模型查询

公共操作、请求参数和返回类型的权威定义在 `src/qmt_rpyc/contracts/operations.py`。
当前发行版本为 `0.9.0`，包含 24 项固定操作；SDK 调试入口独立于公共契约。
内部契约兼容编号为 `9`，不是软件发行版本。详见[版本规则](../versioning.md)。

K 线仅支持日 K；已移除日内查询及分钟/小时历史下载。客户端与服务端须同步升级，
旧版契约协商会失败，不会静默把日内请求改成日线。

财务查询及下载仅支持 Balance、Income、CashFlow、Capital、PershareIndex 五张核心表，默认全选五表。
股东户数、十大股东与十大流通股东的表名、记录模型及响应字段已从公共契约移除，适用于所有 adapter。
请求已移除的表在执行前报 `INVALID_ARGUMENTS`。未请求的表为 null，
已请求且源端确认无记录的表为空数组，不用空数组掩盖不支持或缺失数据。

## 按需查看接口

```sh
qmt-rpyc-client api list
qmt-rpyc-client api list options
qmt-rpyc-client api describe options.get_option_chain
qmt-rpyc-client api describe market.get_ticks --json
```

`list` 每行列名称与用途；`describe` 展示请求必填项、默认值、类型、枚举、
嵌套返回模型、字段含义及操作错误。`--json` 输出带说明的结构，`--compact` 输出单行 JSON。
这些命令不连接服务器，展示本机安装包的契约。运行时可用性通过 `system.get_capabilities` 查询。

需要原始契约及指纹时，在工程根目录运行：

```sh
python scripts/dump_contract.py --output /tmp/qmt-contract.json
```

导出文件是当前代码的派生物，不作为另一份手工维护的接口清单入库。

## 代码入口

以下为源码仓库路径。Windows 安装包用户可直接通过上述 CLI 查询已安装契约。

| 范围 | 权威定义 |
| --- | --- |
| 操作名称、请求/返回类型、有副作用标记、契约指纹 | `src/qmt_rpyc/contracts/operations.py` |
| 模型字段、默认值及校验 | `src/qmt_rpyc/contracts/{market,options,instruments,reference,financials,trading,downloads,system,common}.py` |
| API 用途、字段含义与单位说明 | `src/qmt_rpyc/contracts/documentation.py` |
| 递归类型描述 | `src/qmt_rpyc/contracts/schema.py` |
| 请求/响应对应关系校验 | `src/qmt_rpyc/contracts/validation.py` |
| JSON 编解码 | `src/qmt_rpyc/transport/codec.py` |

说明文案不进入契约指纹；字段、类型、默认参数及行为修订进入指纹。
公开操作和嵌套字段的说明完整性由测试约束。

## 公共约定

- 模型为冻结 dataclass；Python 序列为 tuple，JSON 中为数组。null 与空数组含义不同。
- 日期用 YYYY-MM-DD；时刻必须含时区，线协议统一 UTC。日线标识使用上海交易日。
- 身份字段是不透明字符串；保留券商后缀，不能全局把 SHO/SZO 改为 SH/SZ。
- 批量最多 500 个不重复代码；结果逐项对应，失败不填零或静默丢弃。
- 当前到期日和期权链只服务未到期合约（含当天）；详情按给定代码查询，不伪造历史目录。
- 下载完成只表示源调用结束，不保证数据完整或最新；未知下单、撤单和任务创建结果不可自动重试。

完整语义及实现边界见[架构](../design/architecture.md)。
原始 SDK 的动态参数和结果通过[调试入口](debug.md)调查，不作为应用稳定接口。

## 委托定价

`trading.submit_order` 必须显式指定 `pricing`，不提供默认值或自动降级：

| pricing | SDK 报价类型 | price |
| --- | --- | --- |
| `LIMIT` | `FIX_PRICE=11` | 必须为有限且大于零的限价 |
| `LATEST_PRICE` | `LATEST_PRICE=5` | 有限非负参考价格原样传给 SDK；省略或 null 时传 0 |

Python 调用的 `pricing`、`price` 为关键字参数。限价不会被转换为最新价；
LATEST_PRICE 保留已有调用方的参考价格，参考价格不构成限价约束。
委托查询识别以上两种模式；未知源类型返回 `UNKNOWN`，原始价格类型可通过原生调试接口核查。
SDK 声明类型不代表每个品种和账户均能使用；不自动替换券商拒绝的委托类型。
`0.5.0.dev4` 调整了契约指纹，客户端与服务端需同步升级。


## 0.6.0 迁移说明

公共契约 v4，客户端和服务端同步升级。TradingReference.source_is_trading 与
settlement_price 允许 null，分别表示源端未提供可交易标记或结算字段；false、0 均保留。
BigQMT 的 SettlementPrice 是证券资料中的前结算字段，不用快照当日结算价替代。
其余交易参考字段仍要求有效来源值。

BigQMT 期权标的列表由持久层按上海自然日管理；缺失、过期或 refresh=True 时
同步查询来源，失败不返回过期值，不启动后台刷新。存储故障通过 health.persistent_data 报告。

## 0.7.0 迁移说明

公共契约 v7，客户端和服务端须同步升级。交易操作数量和业务状态语义不变。
删除 Asset/Position/Order.source_account_type、Position.frozen_volume/
on_road_volume/yesterday_volume、Order.source_order_type/source_price_type、
Order/OrderRequest.strategy_name，以及提交/撤单结果的 source_code。
保留 source_status、source_status_message、correlation_ref 和两种撤单身份。
策略名由业务应用管理；adapter 在 SDK 的策略名参数位置传空字符串，
correlation_ref 仍透传投资备注，不能拿策略名代替订单关联标记。
撤单 succeeded 仍仅表示请求成功，不代表订单已经撤销。

从 0.5.1 升级的消费端还须移除日内 K 线能力检查与调用，修改契约版本检查，
同步 requirements 与启动自动升级目标。完整 QMT 的嵌入策略也须替换为 0.7.0
发行包内文件：公共契约与桥协议兼容编号统一为 7，旧组件不能混用。

## 持久数据读取

八项读取增加 `refresh=False`：日 K、交易日历、分红事件、财报、证券资料、期权条款、期权标的集合和到期日集合。`refresh=True` 跳过桥接缓存；复权日 K 同时刷新原始行情和事件集合。失败不返回旧值，不触发下载或后台任务。具体有效期与来源覆盖限制见[持久数据设计](../design/persistent-cache.md)。

结束日为上海当天的日 K 与交易日历请求由服务端内部切分：缓存已证明的历史段复用，未证明部分回源，当天段不落盘，返回仍是单一的原契约结果；调用方不需要新增参数，契约指纹不变，来源不可用时含当天的请求依然失败。复权与补齐由服务端本地派生（含股改事件的复权除外）：来源只提供未复权日 K、分红事件与交易日历。

日 K 请求的起点也由服务端解析：未设置 `start` 时取结束日期前一年（结束日期缺省为上海当天），起点早于来源报告的上市日期时以该日期为准；解析后的窗口即回源与落盘范围，因此不会再返回上市前由来源补齐出的行。上市日期未知时保持调用方传入的起点。该行为变更将 manifest 的 `behavior_revision` 升到 2，客户端与服务端须同步升级；`get_daily_bars` 不再有"起点不限制"的读法。

日 K 请求新增可选 `event_cutoff`（日期）：本地派生复权时只应用不晚于该日期的分红/除权事件，让历史 as-of 请求保持因果（默认 `None` 时仍锚定最新事件）。设置该字段时服务端必须能本地派生该证券的复权；无法派生时该证券返回错误，不回源取得"锚定最新"的结果。该请求字段使 `behavior_revision` 升到 3，客户端与服务端须同步升级。

已移除 reference.list_sectors、reference.get_sector_members、downloads.start_sectors；旧客户端指纹协商失败。
