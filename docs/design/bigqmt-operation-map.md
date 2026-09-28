# 完整 QMT 操作映射调查

范围以 `contracts/operations.py` 的 27 个操作为准。新适配器必须只使用完整 QMT
内置策略能力；本表是源码调查结果，不是目标券商终端的支持声明。

当前已完成非交易 provider 的独立装配和只读管道接入，详见[联调说明](bigqmt-readonly-validation.md)。
表中的待验证项仍保留，不因代码已接通而视为实机验证成功。
交易服务器不可用，按用户要求暂停交易测试；五个交易操作在新 adapter 中明确报告不可用。

**最新范围调整（2026-09-28）：** 用户确认财务暂时无使用场景，忽略股东信息。
新 adapter 仅实现 Balance、Income、CashFlow、Capital、PershareIndex；
用户随后授权从公共接口移除股东信息：财务查询/下载只接受这五表，
股东表名、记录模型和响应字段已统一删除；所有 adapter 的股东请求均在执行前报 INVALID_ARGUMENTS。

参考依据为 `xtquant_big_convert/src/bigqmt_signal_trader/` 下的文件：
`M` = `adapters/market_bigqmt.py`，`O` = `adapters/order_bigqmt.py`，
`P` = `adapters/position_bigqmt.py`。表中行号用于定位参考实现。
`C` 指 QMT 注入的 ContextInfo；全局函数由 QMT 策略环境提供。

“直接”表示参考源码有内置 API 调用；“组合”表示需要从多个事实形成公共结果；
“候选”表示尚未证明目标入口及其语义；“服务”表示由 qmt-rpyc 管理。
参考实现中的原生 xtdata 回退均不能作为新适配器的实现。

| 公共操作 | 纯策略候选路径 | 尚需验证或补齐 |
|---|---|---|
| `reference.list_sectors` | 候选：目标终端注入的全局 `get_sector_list(node)` | 随附资料说明返回板块与子目录两列表；需验证并遍历真实目录，不能套用参考终端缺少全局入口的结论。 |
| `reference.get_sector_members` | 直接：`C.get_stock_list_in_sector(name[, timetag])`（M:1231） | 真实代码后缀、名称和空结果含义。 |
| `instruments.list_option_underlyings` | 直接＋组合：`C.get_option_undl_data("")`（M:2311） | 全市场映射方向、标的身份、当前有效合约筛选。 |
| `instruments.get_details` | 直接：`C.get_instrumentdetail(code)`（M:1210） | 公共模型要求的名称、日期、证券与交易所标识、股本和乘数；期权补充资料。 |
| `instruments.get_trading_reference` | 直接候选：同一证券资料入口 | 部分资料必需值可能为 None。尚无证据将停牌状态等同 IsTrading，或用行情结算价替代资料结算价。 |
| `options.get_expiry_dates` | 组合：标的合约映射＋`C.get_option_detail_data(code)`（M:2250、2311） | 实际标的归属与到期日，按上海日期包含当天，不能推定固定到期月份。 |
| `options.get_option_chain` | 组合：同上；另有 `C.get_option_list(...)`（M:1843） | 后者日期与可用性参数须验证，不能直接等同当前发现语义。 |
| `options.get_contract_details` | 已实现独立 Provider：期权资料＋证券资料（M:2250） | 目标 CALL 样例已确认小写 optType；整值浮点合约单位严格转换。待接入桥与端到端验收，PUT 实机值域仍待验证。 |
| `market.get_ticks` | 直接：`C.get_full_tick(codes)`（M:960） | 观测时间、五档、数量单位和必需字段。不能复制隐式订阅或旧行情回退。 |
| `market.get_market_ticks` | 直接候选：`C.get_full_tick(["SH", "SZ"])`（M:984） | selector 的真实覆盖；参考 wrapper 默认过滤为股票，不能沿用该过滤。 |
| `market.get_daily_bars` | 直接：`C.get_market_data_ex(...)`（M:791） | 实测两根日线含全部所需字段；五种复权、fill_data 和日期端点仍待验证。 |
| `market.get_trading_dates` | 组合候选：`C.get_trading_dates(stockcode, start, end, count)`（M:1755） | 首参是证券；参考用代表指数获取日历，需证明符合公共市场日历约定。 |
| `reference.get_dividend_events` | 直接＋组合：`C.get_divid_factors(code[, day])`（M:1544） | 完整区间事件、复权因子及分红配股字段。参考区间路径可能触发下载，不能引入只读操作。 |
| `reference.get_index_weights` | 组合候选：`C.get_sector(index)`＋`C.get_weight_in_index(index, code)`（M:2611；随附策略手册） | 实测可读取当前成分及单股权重；还需验完整性、单位及日期。全局 get_history_index_weight 实测返回日期到历史成分代码列表，不能作为权重。 |
| `financials.get_reports` | 五表 Provider 已实现；raw 标量转换（M:2324） | 关键字段和双日期已实测；有序逐项结果、缺值和错误处理已覆盖测试，待生产桥接入。股东表已排除，不再阻塞。 |
| `downloads.start_history` | 已实现：用户批准的兼容空实现＋服务任务 | 不触发底层调用、不驱动范围同步；完成只表示兼容请求处理结束。 |
| `downloads.start_financials` | 已实现：同上 | 表和日期仍按公共请求验证，但不驱动数据获取。 |
| `downloads.start_sectors` | 已实现：同上 | 不要求内置板块下载入口。 |
| `downloads.start_index_weights` | 已实现：同上 | 不要求内置权重下载入口。 |
| `downloads.get_task` | 服务：现有任务管理 | 返回真实任务状态、时间；进度为 None，不把兼容任务当刷新屏障。 |
| `trading.get_asset` | 直接：全局 `get_trade_detail_data(account, type, "ACCOUNT")`（P:308） | 主策略线程约束、账户类型与四项资产字段，不能以默认零代替缺失值。 |
| `trading.list_positions` | 直接：同入口 `"POSITION"`（P:183） | 所有数量、价格、市值和来源类型；不能聚合后丢失原始持仓行。 |
| `trading.list_orders` | 直接：同入口 `"ORDER"`（O:709） | 两类编号、时刻、类型、原始状态、成交事实、策略名、关联备注及可撤判定。 |
| `trading.submit_order` | 直接＋异步协调：全局 `passorder(..., C)`（O:559） | 目标部署声明返回 int，实际含义尚未验证；确认真实编号及关联后才能返回 Submitted，不能直接把整数当编号或超时重下。 |
| `trading.cancel_order` | 直接＋身份映射：全局 `cancel(sysid, account, type, C)`（O:688） | 两类撤单定位与市场约束、返回值语义；请求成功不同于订单最终撤销。 |
| `system.get_health` | 服务＋桥连接状态 | 服务存活、策略可达与业务入口可用性须区分。 |
| `system.get_capabilities` | 服务＋兼容性探测 | 操作集合固定；入口存在不能独自证明字段和行为兼容。 |

## 财务表范围与历史调查

参考映射来自 M:463；字段要求以本项目 `contracts/financials.py` 为准。
参考字段清单只是调用请求的证据，不证明字段在目标终端实际返回。

| 公共表 | 内置表名候选 | 重点差异 |
|---|---|---|
| Balance | ASHAREBALANCESHEET | 五项数值及双日期已实测，转换已实现。 |
| Income | ASHAREINCOME | 含两项 EPS 的全部公共字段已实测，直接读取本表，转换已实现。 |
| CashFlow | ASHARECASHFLOW | 含期末现金的全部公共字段已实测，转换已实现。 |
| Capital | CAPITALSTRUCTURE | 数值及双日期已实测；本地文档明确自由流通股本新旧名等价，已映射至 freeFloatCapital。 |
| 已移除：HolderNum | SHAREHOLDER | 公共模型及转换已移除，仅保留历史探针。 |
| 已移除：Top10Holder | TOP10HOLDER；get_top10_share_holder(..., "holder", ...) | Series 缺公告日，raw 和 frame 样例均只有 rank=10 一行；已从接口移除，不再探测。 |
| 已移除：Top10FlowHolder | TOP10FLOWHOLDER；get_top10_share_holder(..., "flow_holder", ...) | 同上，公共范围已移除。 |
| PershareIndex | PERSHAREINDEX | 六项数值及双日期已实测，转换已实现。 |

还需验证报告日期与公告日期、表未请求与请求后无记录的区别、可选数值为空时的含义。
不能把日期缺失当作合法的空表；源端缺失的可空数值依公共字段说明返回 null。

## 当前取证范围

环境与入口、管道回显和阶段 3～8 结果已取得目标策略证据。
阶段 8 仍只返回末位股东；该方向因用户收敛需求而关闭，不再要求后续财务探针。
账户、持仓和委托字段只读样本已取得，后续模拟交易验证见 [交易探针](bigqmt-trading-probe.md)。
尚未验证的数据映射仍不能视为实机验收通过。
兼容下载的含义见 [ADR 0004](../adr/0004-bigqmt-compatibility-downloads.md)。
