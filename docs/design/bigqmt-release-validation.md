# BigQMT 发布前实机验收

2026-09-28，对私有协议 4 Windows 测试部署进行检查。本轮不下单、不撤单、不修改部署配置。
四项下载为此前约定的兼容空实现，仅验证任务生命周期，不证明数据下载或覆盖完整。

本地完整测试：521 passed、5 skipped。实机检查：22 项通过、1 项失败；检查项包含
多个操作组合及财务日期口径，并不等于公共操作数量。健康和能力协商已在前序验证通过。

| 检查项 | 结果 | 耗时（秒） | 摘要 |
|---|---|---:|---|
| ticks | passed | 0.38 | 2 |
| daily | passed | 0.6 | [2, 2] |
| dates_SH | passed | 0.8 | 2 |
| dates_SZ | passed | 0.8 | 2 |
| instrument | passed | 2.4 | 2 |
| trading_reference | failed | 1.3 | ["MISSING_RESULT", "MISSING_RESULT"] |
| underlyings | passed | 63.5 | 9 |
| options_three_operations | passed | 2.99 | [4, 22, 2] |
| sectors | passed | 7.81 | 2711 |
| sector_members | passed | 0.9 | 300 |
| weights | passed | 17.2 | {"count": 300, "sum": 100.0} |
| dividends | passed | 0.9 | 2 |
| financials_report_time | passed | 0.9 | {"Balance": 1, "Income": 1, "CashFlow": 1, "Capital": 1, "PershareIndex": 1} |
| financials_announce_time | passed | 0.9 | {"Balance": 1, "Income": 1, "CashFlow": 1, "Capital": 1, "PershareIndex": 1} |
| market_ticks | passed | 18.02 | {"count": 50629, "failures": 0} |
| asset | passed | 0.34 | {"present": true} |
| positions | passed | 0.7 | 9 |
| orders | passed | 0.8 | {"count": 2, "statuses": ["REPORTED", "REPORTED"]} |
| cancelable_orders | passed | 0.89 | 1 |
| compatibility_download_history | passed | 0.01 | "completed" |
| compatibility_download_financials | passed | 0.01 | "completed" |
| compatibility_download_sectors | passed | 0.0 | "completed" |
| compatibility_download_index_weights | passed | 0.01 | "completed" |

## 发布结论

尚未满足“其他接口没有问题”的发布条件，不创建发布标签，不推送或发布。

1. instruments.get_trading_reference 的两个样本均为 MISSING_RESULT；经原生只读 debug
   核对，IsTrading 与 SettlementPrice 都为 None。不能把缺失值填成 false 或 0。
2. 底层标的首次扫描 63.5 秒，超过默认客户端 30 秒。本次使用 120 秒等待；缓存命中
   的低耗时不能替代首次读取验收。
3. 委托查询可正常解码，但先前一次模拟提交产生两条同备注源记录，关联器保守返回 unknown。
   用户终端显示相同订单编号、仅一条有合同编号；尚未建立经过验证的原生字段归并规则。
4. 用户回报柜台撤单错误 409：“已成交或已撤单”。这一证据不能区分成交与撤销；本轮查询
   仍为两条 REPORTED 且一条可撤，来源状态与柜台回报不一致。不能宣称撤单最终状态已验收。

财务五表两种日期口径、期权链和合约、日 K、快照、交易日历、证券资料、板块、权重、
分红、资产和持仓查询均已取得本轮成功结果。长期压力及原生 I/O 恢复验收不包含在本轮。


## 0.6.0 发布决定与修正

用户明确决定本次沿用实机状态语义发布，后续处理柜台状态刷新差异。没有把错误 409
当作已撤销，也没有进一步发送交易请求。新版本在一致参数的同备注记录中，使用唯一带
柜台编号的记录确认提交；多个柜台记录仍是 unknown，查询保留全部原生记录。
两个缺失交易参考字段允许 null；底层标的持久化最近成功列表并后台刷新，冷启动等待有界，
刷新使用最多三个并行管道请求（原生方法仍在策略线程执行）。旧版验收结果见上文，
这些最终修正以新增回归测试验证，不宣称重新进行了实盘交易或长期压力验收。
