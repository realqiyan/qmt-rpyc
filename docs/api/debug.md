# 原始 SDK 调试入口

用于调查实际券商 SDK 的方法、参数和返回数据。它独立于 28 个稳定业务操作，
不进入业务契约指纹，也不承诺源方法或返回类型稳定。Python 和 CLI 调试连接只做
现有 socket 认证，不进行业务契约协商，所以业务模型不匹配时也能调查服务端。

## 开启

在 Windows 服务端实际使用的 `config.env`（或进程环境变量）中设置：

```dotenv
QMT_RPYC_DEBUG=1
```

重启 `qmt-rpyc-server start` 后生效，启动信息会显示 `SDK debug: on`。
默认关闭；关闭时即使认证成功也返回 `DEBUG_DISABLED / not_executed`。
调试沿用共享密钥和 TLS 配置，权限与该实例的受信任管理员相同。
不需要部署额外 HTTP 服务，也不自动修改现有服务端配置。

## CLI

```bash
# 获取实际部署的方法、签名、文档和常量
qmt-rpyc-client debug --profile office describe
qmt-rpyc-client debug --profile office describe xtdata.get_option_detail_data
qmt-rpyc-client debug --profile office describe xtconstant.STOCK_BUY

# 原始位置参数与关键字参数
qmt-rpyc-client debug --profile office call xtdata.get_full_tick \
  --args '[["510050.SH"]]'
qmt-rpyc-client debug --profile office call xtdata.get_instrument_detail \
  --args '["510050.SH"]' --kwargs '{"iscomplete":true}'
```

`debug call` 的目标为 `xtdata.NAME` 或 `trader.NAME`。Trader 查询之外的方法
需要 CLI 的 `--confirm-trading`；这只是命令行操作确认，不改变原始 SDK 参数。
常量用 `describe xtconstant.NAME` 读取。选项如 `--profile`、`--timeout` 放在
`debug` 后、`call/describe` 前。

## Python

```python
from qmt_rpyc.client.debug import DebugClient, DebugError

with DebugClient.connect_profile("office") as client:
    print(client.describe("xtdata.get_option_list"))
    result = client.call(
        "xtdata.get_instrument_detail",
        args=["510050.SH"],
        kwargs={"iscomplete": True},
    )
    print(result)  # SDK 字段名，无业务模型投影
```

也可调用 `DebugClient.connect(host, port, auth_key=..., timeout=...)`。
`DebugError.error` 保留类型、消息、阶段及执行结果是否未知。

## 转发与表示边界

- 仅支持上述三个 SDK 对象的直接公开成员；不支持任意模块导入、嵌套属性路径或下划线私有成员。
- 原样传递 JSON 位置参数和关键字参数，不绑定业务请求模型，不补 SDK 默认值，
  不做期权名称补充、字段筛选、单位转换或批量错误包装。
- Trader 复用当前共享实例、原生锁及连接检查。其 `account` 参数中的账户字符串仍按
  已有连接管理逻辑转换为 `StockAccount`，无需在客户端安装 xtquant。
- 下载也直接同步执行，返回 SDK 原始结果，不创建业务下载任务；可能需要提高客户端 timeout。
- 返回值使用现有 SDK JSON 序列化器：DataFrame 为 `columns/index/data`，ndarray、tuple、set
  为列表；numpy 标量为 Python 标量；日期为来源的 ISO 字符串；bytes 为 base64；Decimal
  为字符串；NaN、Infinity、pandas 缺失值为 null；字典键转换为字符串。SDK 对象导出公开字段。
  因此它保留源字段和值的可传输表示，**不是原生 Python 对象或类型无损镜像**；键类型、集合顺序、
  非有限数与 null 的区别不能依赖此表示。循环和超过 64 层的内容会返回序列化标记。
- 不支持远程函数回调、原生对象参数、pickle 或任意 Python 执行；这些需要更复杂的协议。
- 请求最多 1 MiB。大结果不主动截断，应由调用方缩小查询范围。

调用会真实执行 SDK，包括下载、下单、撤单及其他有副作用的方法。
SDK 执行后的异常、响应无法解析或连接中断均保守报告 `unknown`，从不自动重试；
超时不代表 SDK 调用已取消。执行前拒绝为 `not_executed`。
日志记录目标方法，不记录完整参数；源异常详情会返回给已认证调试调用方。

验证使用合成 SDK 和本地 RPyC socket；Windows/QMT 的具体方法、回调和传输行为仍以实际部署验证为准。
