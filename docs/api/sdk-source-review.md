# 当前券商 SDK 定义审阅

证据：[contract-v1-sources.json](contract-v1-sources.json)，2026-09-23 用户更新后的完整采集。SHA-256：`eefd7425e19a8a3544f96a7ab84f32c43dabdeb149661ed8c9dd32c37064c615`。22 个公开接口、7 个类型和 4 个内部 helper 均成功采集定义，没有 inspection_errors；另有 16 个常量和模块文件指纹。22 个公开签名与 16 个常量均与原始部署基线一致。本记录只分析定义，没有执行其中的代码或调用 SDK，用户提供的证据文件未作修改。

模块文件指纹已取得；xtquant 自身版本仍为空。文件指纹可以识别这些 Python 模块的变化，不等于 MiniQMT 或全部原生依赖的完整构建身份。

后续已通过升级后的 0.3.1 客户端取得只读 RPC 样本，补充确认常用 K 线索引、分红列、期权嵌套/名称差异和财务空表等。以下保留源码证据本身的边界；新增实测结论及剩余缺口见[返回模型与实测对照](contract-v1-returns.md)，不再要求重复采集已有资料。

## 已获得的字段与行为证据

- `XtAsset` 定义 `account_type, account_id, cash, frozen_cash, market_value, total_asset`。
- `XtOrder` 定义 `account_type, account_id, stock_code, order_id, order_sysid, order_time, order_type, order_volume, price_type, price, traded_volume, traded_price, order_status, status_msg, strategy_name, order_remark`。其中 `order_sysid` 注释为柜台编号，可支持按柜台编号撤单的契约字段。
- `XtPosition` 定义 `account_type, account_id, stock_code, volume, can_use_volume, open_price, market_value, frozen_volume, on_road_volume, yesterday_volume`。
- 上述构造函数没有完整类型约束；原生 Trader 实际返回的对象是否由这些 Python 类构造仍需验证。委托/持仓数量的注释明确股票用股、债券用张，不能扩展成行情 volume 单位也已获证明。
- `order_stock` 实现返回 `resp.order_id`。其 docstring 称请求序号，不应据此把同步下单契约定义为异步 seq。
- `query_stock_asset` 在无结果时返回 None；“无结果”和“零资产”必须区分。

## 下载完成的边界

`download_history_data` 的文档声明 bool，函数实际没有 return，正常返回 None。财务、板块、指数权重三个下载函数也没有 return。因此任务成功不能取决于 `bool(SDK返回值)`。用户在 Q9 确认：任务的 completed 表示 SDK 调用正常返回，不单独证明请求范围的数据已经完整可读。后续读取与数据完整性应单独判断。

## 行情与分红

- `get_market_data_ex` 对 `1m/5m/15m/30m/1h/1d` 直接委托给 `_get_market_data_ex_ori_221207`。新取得的 helper 明确将原生 `(stock, index, npdatas)` 转为 `{stock: DataFrame(data, index)}`；常用 K 线按证券组织的返回形态已有源码证据，DataFrame 的 index 值仍来自原生返回，时间格式和单位需样本验证。
- 常用周期 helper 调用原生 `get_market_data3` 的 v4 格式，默认允许本地和服务端读取；不能据此承诺此查询不会访问远端。其他周期经 `get_market_data_ex_ori` 调用 v3 格式，后续转换中 field_list 是否为空会影响 index。
- `timetag_to_datetime` 的定义仅调用 `timetagToDateTime`；其文档声明毫秒输入，但未提供底层时区处理证据。取得函数定义不等于所有行情时间格式已确认。
- `get_divid_factors` 确认返回 `pd.DataFrame(native_result).T`，因此经当前序列化器后是 split 表。这支持 a-options 解析不匹配的判断，但仍不能证明 dr/interest/factor 的列名、逐次/累计语义与具体单位。

## 期权适配不能照搬旧故障描述

- 当前采集到的相关实现未使用 `OptUndlUniCode`。a-trader 的历史故障记录不能直接描述成本次部署的已复现错误。
- `get_instrument_detail(iscomplete=True)` 保留完整字典中的 `ExtendInfo`，仅转换部分日期；没有将所有扩展字段提升到顶层。a-trader 对顶层 OptUndlCode/OptionType 的读取需要与实际对象样本核对。
- `get_option_detail_data` 从原生 instrument 的 `ExtendInfo` 取 OptUnit、OptUndlCode、OptUndlMarket、OptExercisePrice 等；字段值可能为 None。其输出名单没有 InstrumentName，适配器若要提供该字段，应从完整资料明确补入。
- 期权类型通过 InstrumentName.find 推断；名称缺失可能抛错，识别失败返回空 optType。固定契约不能把未知类型默认为 PUT。
- `get_option_undl_data(None)` 明确返回标的到合约列表的字典；指定标的返回列表。内部只检查键存在即拼接 OptUndlCode 与 OptUndlMarket，不能据此保证 None 值不会报错。
- `get_option_list` 的六位 dedate 按到期月份筛选；八位 dedate 主要按上市/创建日期筛选，只有 isavailavle=True 时才排除该日前已到期的合约。它不等于精确到期日筛选；用户在 Q8 确认 V1 保留这些现有含义。

补查 a-options 后确认 `core/market_data/_chain.py:349` 之后还按 `exps[expire]` 选择所需到期日；因此这里首先是上游筛选与命名的差异，不能据此声称使用方一定展示了错误到期日。按 Q8 继续由使用方完成该筛选，不把 SDK 参数重新定义为精确到期日。

## 财务和指数

- `get_financial_data` 实际返回证券到表名到 DataFrame 的嵌套字典，不是 docstring 描述的 field/date/stock/value。
- 默认财务查询有 8 张表，默认下载有 9 张，多出 PerShare。V1 的空 table_list 需要固定为明确的表集合。
- 财务日期转换依赖 time.localtime；当前源码存在 NaN 分支可能继续转换未修正的 NaN 的路径，需要单独验证。财务列名和单位仍需样本。
- `get_index_weight` 只是调用原生 get_weight_in_index；返回键和值的单位尚无完整证据，不能假定比例或百分数。

## 撤单定义与结果边界

[qmt-api-baseline-20260923.json](qmt-api-baseline-20260923.json) 已确认两个同步撤单和两个异步变体存在，更新后的源码采集已包含两个同步实现。V1 纳入同步接口：

| 桥接调用签名 | 当前 SDK 文档说明 |
| --- | --- |
| `cancel_order_stock(account, order_id)` | 委托编号撤单；0 成功，-1 委托已完成，-2 未找到对应委托，-3 账号未登录 |
| `cancel_order_stock_sysid(account, market, sysid)` | 柜台合同编号撤单；market 为上海 0、深圳 1；0 成功，-1 失败 |

account 在桥接层仍是账户 ID 字符串；两种委托标识不可混用，sysid 应按不透明标识保留，避免数值转换损失格式。两个实现都构造 CancelOrderStockReq 并调用同一个原生撤单入口：第一种设置 m_nOrderID，第二种设置 m_nMarket 和 m_strOrderSysID；等待响应后均返回 `resp.cancel_result`。它们没有把每个返回码的产生条件写在 Python 层，因此表中的码义仍来自部署文档，不是终态保证。

三个响应/错误类型也已取得：`XtCancelOrderResponse` 包含 account_id、cancel_result、order_id、order_sysid、seq；`XtCancelError` 包含账户、委托标识、market、error_id、error_msg；`XtOrderResponse` 包含 order_id 与 seq 等。类型定义不能单独证明同步接口实际使用了这些 Python 类。

`common_op_sync_with_seq` 创建 Future，以请求序号登记 SDK 内部回调，发出请求后调用无 timeout 参数的 `future.result()`。所以“同步接口”仍依赖服务端内部 SDK 回调；不需要把这个回调暴露给客户端，但原生响应不来时可能一直等待。客户端 RPC 超时不能证明撤单未发生，也不会自动终止这次 SDK 等待；桥接不能以另发撤单或自动重试来恢复未知结果。原生等待的隔离/恢复不在目前签名检查的保证范围内。

本轮所列定义缺口已经补齐，不再要求重复采集。后续需要的是实际只读返回结构及时间、单位、空值等语义证据；内部 helper 和响应类型仍不额外增加公开 API，也无需执行真实下单或撤单。
