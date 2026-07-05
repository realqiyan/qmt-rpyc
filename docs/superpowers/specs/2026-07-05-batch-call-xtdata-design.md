# batch_call_xtdata — 批量 xtdata 调用端点

**日期**: 2026-07-05
**范围**: 服务端 + 客户端
**目标**: 将期权链查询从 O(N×RTT) 降低到 O(RTT + N/workers)，一次 RPC 往返完成 N 个同函数调用

---

## 1. 协议格式

### 请求

```
exposed_batch_call_xtdata(name, calls)

  name  : str                      — xtdata 函数名，如 "get_option_detail_data"
  calls : [(args, kwargs), ...]   — 同一函数的多组参数
```

- `name` 必须是 xtdata 模块中的公开 callable
- `name` 为 `download_*` 前缀的函数会被拒绝（整批返回 error）
- `calls` 数量硬上限 500（防止内存爆炸），超出返回 `BatchTooLarge`

### 响应

```json
{
    "status": "ok",
    "results": [
        {"status": "ok",   "data": {...}},
        {"status": "ok",   "data": {...}},
        {"status": "error", "error_type": "ValueError", "error_message": "invalid code"}
    ]
}
```

- 顶层 `status: "ok"` — 批量调度层面成功（不代表所有子调用成功）
- 顶层 `status: "error"` — 仅在整体调度失败（如线程池崩溃、参数校验失败）
- `results[i]` 顺序与 `calls[i]` 一一对应
- 每个子项格式与现有 `call_xtdata` 返回值一致

### 错误类型

| error_type | 来源 | 含义 |
|-----------|------|------|
| `AttributeError` | 顶层 | `name` 不存在于 xtdata |
| `BatchRejected` | 顶层 | `name` 是 `download_*` 函数 |
| `BatchTooLarge` | 顶层 | `calls` 数量超过 500 上限 |
| `*` | 子项 | 子调用自身的异常类型（`ValueError`, `KeyError` 等） |

---

## 2. 服务端实现

### 文件: `server/service.py`

新增方法 `exposed_batch_call_xtdata`:

```python
_BATCH_MAX_CALLS = 500
_BATCH_MAX_WORKERS = int(os.environ.get("QMT_BATCH_MAX_WORKERS", "50"))

def exposed_batch_call_xtdata(self, name, calls):
    self._require_authed()

    # 参数校验
    if len(calls) > _BATCH_MAX_CALLS:
        return {
            "status": STATUS_ERROR,
            "error_type": "BatchTooLarge",
            "error_message": f"max {_BATCH_MAX_CALLS} calls per batch, got {len(calls)}",
        }
    if is_download_function(name):
        return {
            "status": STATUS_ERROR,
            "error_type": "BatchRejected",
            "error_message": f"'{name}' is a download function; use call_xtdata",
        }
    if xtdata is None:
        return {
            "status": STATUS_ERROR,
            "error_type": "ImportError",
            "error_message": "xtquant not available",
        }

    fn = getattr(xtdata, name, None)
    if fn is None:
        return {
            "status": STATUS_ERROR,
            "error_type": "AttributeError",
            "error_message": f"xtdata has no attribute {name!r}",
        }

    # 并发执行
    max_workers = min(len(calls), _BATCH_MAX_WORKERS)
    results = [None] * len(calls)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_execute_one, fn, args, kwargs): i
            for i, (args, kwargs) in enumerate(calls)
        }
        for f in as_completed(futures):
            i = futures[f]
            try:
                results[i] = f.result()
            except Exception as e:
                results[i] = {
                    "status": STATUS_ERROR,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                }

    # 日志
    ok_count = sum(1 for r in results if r.get("status") == STATUS_OK)
    err_count = len(results) - ok_count
    self._log_request(
        "batch_call_xtdata",
        f"fn={name}, calls={len(calls)}, ok={ok_count}, err={err_count}"
    )

    return {"status": STATUS_OK, "results": results}
```

### 辅助函数（模块级）

```python
def _execute_one(fn, args, kwargs):
    """执行单个 xtdata 调用。独立函数以便 ThreadPoolExecutor 使用。"""
    args = [_materialize(a) for a in args]
    kwargs = {k: _materialize(v) for k, v in kwargs.items()}
    raw = fn(*args, **kwargs)
    return {"status": STATUS_OK, "data": serialize(raw)}
```

- 复用现有 `_materialize()` 和 `serialize()`
- 异常被 `as_completed` 循环中的 `f.result()` 抛出后统一捕获

### 并发策略

- 每次批量调用创建独立的 `ThreadPoolExecutor`，用完即弃
- `max_workers = min(len(calls), 50)` — 最小化线程创建开销，最大 50 并发
- xtdata 的 C++ 层（pybind11）无 GIL，数据查询函数是 I/O 密集型（读本地缓存/QMT 进程通信），Python 线程池适用
- 不占用 `DownloadTaskManager` 的线程池——两者独立

### 文件: `server/main.py`

```python
# 新增 .env 读取（可选覆盖默认值）
"batch_max_workers": int(os.environ.get("QMT_BATCH_MAX_WORKERS", "50")),
```

最终 `_BATCH_MAX_WORKERS` 从 `service.py` 模块级读取环境变量，`main.py` 只负责文档化。

---

## 3. 客户端实现

### 文件: `client/proxy.py`

`_RemoteCallable` 新增 `batch` 方法:

```python
import types

class _RemoteCallable:
    def __init__(self, client, surface, name, meta):
        self._client = client
        self._surface = surface
        self._name = name
        self._meta = meta
        self.__doc__ = meta.get("doc", "")
        self.__name__ = name
        # types.MethodType puts batch into self.__dict__, so __dir__ finds it
        self.batch = types.MethodType(self._batch, self)

    def __call__(self, *args, **kwargs):
        return self._client._call(self._surface, self._name, args, kwargs)

    def _batch(self, calls):
        """Execute this function in batch mode.

        Send multiple (args, kwargs) pairs in a single RPC call, executed
        concurrently on the server.

        Args:
            calls: list of (args, kwargs) tuples — each is (args, kwargs)

        Returns:
            list of dicts, each {"status": "ok", "data": ...}
                         or {"status": "error", "error_type": "...", "error_message": "..."}
            in the same order as the input calls.

        Raises:
            QmtError: if the overall batch dispatch fails (e.g. connection lost,
                      server rejects download_* function, or batch too large)

        Example:
            codes = client.xtdata.get_option_list("510050.SH", "")
            results = client.xtdata.get_option_detail_data.batch([
                ([code], {}) for code in codes
            ])
            ok = [r["data"] for r in results if r["status"] == "ok"]
        """
        return self._client._batch_call(self._surface, self._name, calls)

    def __dir__(self):
        return list(self.__dict__.keys())
```

`dir()` 可发现 `batch`：`types.MethodType` 将其放入 `self.__dict__`，`__dir__` 返回 `self.__dict__.keys()` 会包含它。

### 文件: `client/client.py`

`QmtClient` 新增 `_batch_call` 方法:

```python
class QmtClient:
    def _batch_call(self, surface, name, calls):
        """Dispatch a batch call to the server."""
        if surface != "xtdata":
            raise ValueError(f"batch_call only supports xtdata, got {surface}")
        resp = self._conn.root.batch_call_xtdata(name, calls)
        if resp.get("status") != "ok":
            raise _map_error(resp)
        return resp["results"]
```

- 整体调用失败（连接断开、认证失败）→ 抛异常，与现有 `_call` 行为一致
- 子调用失败 → 不抛异常，由调用者自行分拣 `results`

---

## 4. 使用示例

### 期权链批量查询

```python
from client.client import QmtClient

client = QmtClient.connect("192.168.1.100", auth_key="your-secret")

# 1. 获取期权合约列表
codes = client.xtdata.get_option_list("510050.SH", "")
print(f"Found {len(codes)} option contracts")

# 2. 批量获取期权详细数据（一次 RPC，服务端并发 50）
results = client.xtdata.get_option_detail_data.batch([
    ([code], {}) for code in codes
])

# 3. 分拣成功/失败
ok = [r["data"] for r in results if r["status"] == "ok"]
errors = [
    (i, codes[i], r["error_message"])
    for i, r in enumerate(results)
    if r["status"] == "error"
]
print(f"OK: {len(ok)}, Errors: {len(errors)}")
```

### 批量获取行情数据

```python
# 一次获取多只股票的多日行情
stock_list = ["600000.SH", "000001.SZ", "000002.SZ", "600036.SH"]
results = client.xtdata.get_market_data.batch([
    ([], {"stock_list": [s], "period": "1d"}) for s in stock_list
])
```

### 错误处理模式

```python
# 整体失败（连接断开、BatchRejected、BatchTooLarge 等）→ 抛 QmtError
# 子调用失败 → 在 results 列表中体现，不抛异常
results = client.xtdata.get_option_detail_data.batch(calls)

for i, r in enumerate(results):
    if r["status"] == "ok":
        process(r["data"])
    else:
        log.warning(f"call {i} failed: {r['error_type']} - {r['error_message']}")
```

---

## 5. 关键参数

| 参数 | 默认值 | 配置方式 | 说明 |
|------|--------|----------|------|
| `QMT_BATCH_MAX_WORKERS` | 50 | `.env` | 批量线程池最大并发数 |
| 单批最大调用数 | 500 | 硬编码 `_BATCH_MAX_CALLS` | 防止单批内存爆炸 |
| `sync_request_timeout` | 300s | 现有 `.env` | 批量整体超时复用现有配置 |

---

## 6. 涉及文件

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `server/service.py` | 新增方法 | `exposed_batch_call_xtdata` + `_execute_one` |
| `client/proxy.py` | 新增方法 | `_RemoteCallable.batch()` |
| `client/client.py` | 新增方法 | `QmtClient._batch_call()` |
| `client/exceptions.py` | 不变 | 无需新增异常类，`RemoteCallError` 已覆盖整体失败场景 |
| `tests/test_service.py` | 新增测试 | `TestBatchCallXtdata` |
| `tests/test_client.py` | 新增测试 | 客户端 batch 测试 |
| `CLAUDE.md` | 更新文档 | 追加 batch 接口说明 |

---

## 7. 不变更的部分

- **协议格式**: 继续使用现有 `{status, data/error_type/error_message}` 格式
- **序列化**: 复用 `server/serializer.py` 的 `serialize()`
- **netref 物化**: 复用 `_materialize()`
- **认证/鉴权**: 复用 `_require_authed()`
- **DownloadTaskManager**: 不修改，下载仍走 `call_xtdata`
- **ConnectionManager / trader**: 不涉及
- **`sync_request_timeout`**: 不修改，300s 对批量足够

---

## 8. 实现顺序

1. 服务端 `exposed_batch_call_xtdata` + `_execute_one` → 单元测试
2. 客户端 `batch()` + `_batch_call` → 集成测试（用例接 mock server）
3. `.env` 文档更新 + `CLAUDE.md` 更新
