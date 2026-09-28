# QMT 同机跨进程管道探针

本次探针仅回显合成字符串，不连接账户，也不调用任何 QMT 行情、交易或下载函数。
它与能力探针独立，用于验证所选通信机制的实际运行行为，不是生产桥。

## 两端运行

1. 先把 [probe_bigqmt_pipe_client.ps1](../../scripts/probe_bigqmt_pipe_client.ps1)
   保存到同一 Windows 机器，并打开该文件所在目录的 PowerShell。
2. 在完整 QMT 中新建独立 Python 策略，粘贴
   [probe_bigqmt_pipe.py](../../scripts/probe_bigqmt_pipe.py) 的全部内容。
   正常运行，输出 `probe_ready` 和 `pump_observed` 后保持运行。
3. 在准备好的 PowerShell 中执行：

   ```powershell
   powershell -NoProfile -File .\probe_bigqmt_pipe_client.ps1
   ```

4. 外部命令结束后停止 QMT 探针策略，返回 PowerShell 的 JSON 结果和 QMT 中包含
   `QMT_RPYC_PIPE` 的输出。不要同时运行两份同名管道策略。

探针仅使用 Windows 和 Python/.NET 标准组件，不需要安装原生 xtquant、qmt-rpyc、
Redis 或 pyzmq。策略默认最多运行 180 秒，然后停止管道活动；再次验证需重新启动策略。
如果 PowerShell 脚本被本机策略阻止，返回该错误即可。

## 本次覆盖

- 策略回调周期请求为 100ms，能否执行由 `pump_observed` 和实际请求往返判断。
- 4 个固定管道实例，256 KiB 帧长上限；所有连接、读取、写入使用异步 WinAPI。
- 20 次连续消息、90000 字节中文 UTF-8 负载、错误 JSON 后的正常请求。
- 8 次新建连接、4 个同时保持打开的连接；后者不是并发请求压力测试。
- 对端不读取大响应时，其他连接仍能响应。服务端若写入未完成，5 秒后发起取消。
  是否实际进入写超时路径需要结合 `io_timeout` 的 `kind=write` 判断，不能仅凭客户端完成推断。
- 人工延迟回显导致客户端超时，随后新连接成功；客户端不重发超时请求。

超时后的业务执行结果、重复请求去重、策略重启恢复和长期资源稳定性不由此次 echo 探针证明，
后续按[可靠性要求](bigqmt-reliability.md)分别验证。

## 输出判定

外部 `status=passed` 只表示列出的 echo 场景通过，`production_ready=false` 是刻意保留的边界。
策略 `io_timeout(kind=write)` 可由慢读测试刻意触发，不代表测试失败；后续连接仍需成功。
如果出现 `event_close_error`、`pipe_close_error`、`channel_error`、`accept_error`，需要进一步排查。
停止时 `pending_handles_retained` 应为 0；非零表示取消未及时结束，为避免释放仍被内核引用的
内存而保留了对象，本次资源清理验收不能通过。

策略端在定时回调中只发起或轮询非阻塞 I/O，不等待客户端，也不执行业务调用。
生产实现仍需独立验证 I/O 线程、有界业务队列和后台线程的策略沙箱限制。
