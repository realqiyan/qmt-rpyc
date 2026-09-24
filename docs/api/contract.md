# 公共 API 与模型查询

公共操作、请求参数和返回类型的权威定义在 `src/qmt_rpyc/contracts/operations.py`。
当前契约版本为 `2`，包含 28 项固定操作；SDK 调试入口独立于公共契约。

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
