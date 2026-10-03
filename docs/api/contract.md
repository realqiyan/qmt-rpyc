# 公共 API 与模型查询

公共操作、请求参数和返回类型的权威定义在 `src/qmt_rpyc/contracts/operations.py`。
操作清单由契约生成，SDK 调试入口独立于公共契约。发行版本与契约兼容编号的关系见[版本规则](../versioning.md)。

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

内部装配及来源边界见[架构](../design/architecture.md)。
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


## 交易结果与参考字段

`TradingReference.source_is_trading` 与 `settlement_price` 允许 null，表示来源未提供；false 和 0 保留。BigQMT 资料中的前结算字段不以行情当日结算价替代，其余必需参考字段仍要求有效来源值。

`correlation_ref` 透传投资备注，不能用策略名替代订单关联标记。策略名由应用管理，适配器向 SDK 对应参数传空字符串。委托编号与柜台合同编号分别用于对应撤单方式；提交响应不表示成交，撤单 `succeeded` 也不表示最终已撤销。

旧版本字段与操作的迁移历史见 [Changelog](../../CHANGELOG.md)，当前可用字段以本机 API describe 为准。

## 持久数据读取

日 K、交易日历、分红事件、财报、证券资料、期权条款、期权标的列表和到期日集合接受 `refresh=False`。true 时同步回源；复权日 K 同时刷新原始行情和事件集合。失败不返回旧值，不触发 SDK 下载或后台任务。

非 count 日 K 的缺省 start 为结束日期前一年，结束日期缺省为上海当天；起点早于来源报告的上市日期时裁剪至上市日，上市日期未知时不推测。count 保留从 end 向前取指定数量的语义，与 start 互斥，不套用一年起点。

`event_cutoff` 使本地复权只使用不晚于指定日期的事件；默认 None 使用最新事件视图。设置 cutoff 后无法安全本地派生时该证券明确失败，不能用来源锚定最新的结果替代。普通请求无法安全派生时保持完整来源结果。

有效且已证的历史范围可离线读取，复权还需有效的事件依赖；不承诺跨日使用过期事件集合。含当天的查询仍须回源，当天数据不持久化；服务端内部切分不改变返回模型。

各适配器的覆盖限制、相关股改与 front 窗口例外、count 后缀复用条件见[持久缓存设计](../design/persistent-cache.md)。响应成功和下载 completed 均不证明数据已完整缓存。
