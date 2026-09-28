from pathlib import Path
import datetime, hashlib, json, shutil, subprocess, tempfile, zipfile

root = Path(__file__).resolve().parents[1]
import sys
python = Path(sys.executable)
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')
version_scope = {}
exec((root / 'src/qmt_rpyc/version.py').read_text(), version_scope)
version = version_scope['__version__'].split('+')[0] + '+bigqmt.' + stamp
work = Path(tempfile.mkdtemp(prefix='qmt-windows-test-'))
source = work / 'source'
source.mkdir()
for name in ('pyproject.toml', 'README.md', 'LICENSE'):
    shutil.copy2(root / name, source / name)
shutil.copytree(root / 'src/qmt_rpyc', source / 'src/qmt_rpyc', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
(source / 'src/qmt_rpyc/version.py').write_text('__version__ = ' + repr(version) + '\n')
subprocess.run([str(python), '-m', 'build', '--outdir', str(work / 'packages'), str(source)], check=True)
packages = sorted((work / 'packages').iterdir())
subprocess.run([str(python), '-m', 'twine', 'check', *map(str, packages)], check=True)
name = 'qmt-rpyc-' + version + '-windows-test'
bundle = work / name
bundle.mkdir()
wheel = next(p for p in packages if p.suffix == '.whl')
shutil.copy2(wheel, bundle / wheel.name)
subprocess.run([str(python), str(root / 'scripts/build_bigqmt_strategy.py'), '--output', str(bundle / 'bigqmt_strategy.py')], check=True)
shutil.copy2(root / 'scripts/check_bigqmt_bridge.py', bundle / 'check_bigqmt_bridge.py')
shutil.copy2(root / 'LICENSE', bundle / 'LICENSE')

def bat(name, content):
    (bundle / name).write_bytes(content.replace('\n', '\r\n').encode('ascii'))

bat('install.bat', '''@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\\Scripts\\python.exe" goto install
py -3.11 -c "import struct; assert struct.calcsize('P') == 8" >nul 2>&1
if not errorlevel 1 (
    py -3.11 -m venv .venv
    if errorlevel 1 goto failed
    goto install
)
py -3.10 -c "import struct; assert struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 (
    echo Install 64-bit Python 3.11 or 3.10 with the Python launcher first.
    goto failed
)
py -3.10 -m venv .venv
if errorlevel 1 goto failed
:install
".venv\\Scripts\\python.exe" -c "import sys,struct; assert sys.version_info[:2] in ((3,10),(3,11)) and struct.calcsize('P') == 8"
if errorlevel 1 goto failed
".venv\\Scripts\\python.exe" -m pip install --index-url https://pypi.org/simple ".\\WHEEL_NAME[server]"
if errorlevel 1 goto failed
".venv\\Scripts\\python.exe" -m pip check
if errorlevel 1 goto failed
".venv\\Scripts\\python.exe" -c "from qmt_rpyc.version import __version__; print('Installed:', __version__)"
if errorlevel 1 goto failed
echo Installation complete. See README.txt, then run check-readonly.bat.
pause
exit /b 0
:failed
echo Installation failed. Review the error above.
pause
exit /b 1
'''.replace('WHEEL_NAME', wheel.name))

bat('check-readonly.bat', '''@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\\Scripts\\python.exe" (
    echo Run install.bat first.
    pause
    exit /b 1
)
echo Start bigqmt_strategy.py in QMT before running this check.
".venv\\Scripts\\python.exe" check_bigqmt_bridge.py %*
set "QMT_TEST_RESULT=%ERRORLEVEL%"
pause
exit /b %QMT_TEST_RESULT%
''')

bat('start-server.bat', '''@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\\Scripts\\qmt-rpyc-server.exe" (
    echo Run install.bat first.
    pause
    exit /b 1
)
set "QMT_RPYC_ADAPTER=bigqmt"
if not defined QMT_RPYC_DEBUG set "QMT_RPYC_DEBUG=0"
if not exist "config.env" (
    echo Copy your existing config.env into this directory before starting.
    echo Existing host and authentication settings must be preserved.
    pause
    exit /b 1
)
".venv\\Scripts\\qmt-rpyc-server.exe" --config "%~dp0config.env" start
set "QMT_TEST_RESULT=%ERRORLEVEL%"
pause
exit /b %QMT_TEST_RESULT%
:failed
echo Configuration initialization failed.
pause
exit /b 1
''')

bat('start-debug-server.bat', '\n'.join(('@echo off', 'setlocal', 'set "QMT_RPYC_DEBUG=1"', 'call "%~dp0start-server.bat"', 'exit /b %ERRORLEVEL%', '')))

readme = f'''BigQMT Windows 实机测试包
构建版本：{version}

此包用于当前策略桥及交易接口联调，未发布到 PyPI，不是正式版本。
包含当前工作区实现；不包含账户、认证密钥、终端路径或本地日志。

一、准备
1. 解压 ZIP 到一个独立目录，不能直接在 ZIP 内运行批处理。
2. 外部环境需安装 Windows 64 位 Python 3.11（或 3.10）及 py 启动器。
   QMT 内置 Python 3.6.8 无需安装本项目或依赖，也不要修改它。
3. 双击 install.bat。安装到本目录 .venv；需联网访问 PyPI 下载依赖。
   本包不是完整离线依赖包。

二、只读联调（先只做这一步）
1. 停止之前的探针策略。在完整 QMT 新建一个独立策略，将 bigqmt_strategy.py
   按 GBK 编码打开，全部内容复制进去并以 GBK 保存运行，不要同时运行两个同名桥。
2. 看到 QMT_RPYC_BRIDGE ready; read_only=False; protocol=4 后，
   双击 check-readonly.bat。QMT 与该窗口请使用同一 Windows 用户运行。
3. 发回检查窗口的输出及 QMT 相关错误文字即可，不需要账号或订单号。
   此检查只读取快照、两根日 K、证券资料、两个交易日和一个期权样本；
   不读账户，不下单/撤单，不查询财务，不显式下载。
4. 如需更广的只读覆盖，可在命令提示符运行 check-readonly.bat --extended。
   首次先使用默认检查，扩展检查可能较慢。
5. 测试结束在 QMT 停止策略；记录 pending_handles_retained 的值。

三、可选 RPyC 联调
先将原测试目录的 config.env 复制到本目录，保留已有监听地址和认证密钥。
只读检查通过后，双击 start-server.bat。启动脚本不会生成或覆盖配置；
没有 config.env 时提示退出。远程测试需要沿用可访问的监听地址和共享密钥。
请勿发送 config.env。已有系统环境变量可覆盖配置文件；遇到监听冲突请检查本地环境。
客户端与服务均需使用本包 wheel 对应代码（契约 v4，仅日 K、五张财务表）。
新环境不加载 xtquant；已提供 STOCK 账户交易接口，下单需由客户端显式调用。
安装、启动及 check-readonly.bat 不会自动下单。debug 仍只允许只读原生调用。

调试：停止旧服务后运行 start-debug-server.bat，开启经过认证的只读原生调试。
服务 wheel 与 QMT 策略必须一起升级到本包版本（私有协议 4）。
普通启动尊重已有 QMT_RPYC_DEBUG 环境变量，否则默认关闭。

四、验收边界
已有本地自动化测试通过；此包仍需 Windows/QMT 真实数据、管道及长期稳定性验收。
production_ready=false 是当前验收阶段标记。部分必需来源字段为空会明确报错，
不填默认值或伪造成功。不支持的实机形态需要根据结果继续修正。
卸载测试环境可在停止 QMT 策略和本包服务后删除解压目录。

文件
install.bat             创建独立环境并安装随附 wheel 与联网依赖
bigqmt_strategy.py       QMT Python 3.6 GBK 单文件策略
check-readonly.bat       本机只读检查
check_bigqmt_bridge.py   检查脚本
start-server.bat         可选 RPyC 服务启动
start-debug-server.bat   开启只读调试的 RPyC 服务
BUILD.json              构建版本及源码摘要
SHA256SUMS.txt           包内文件 SHA-256
'''
(bundle / 'README.txt').write_text(readme, encoding='utf-8-sig')
manifest = {}
for p in sorted((source / 'src/qmt_rpyc').rglob('*')):
    if p.is_file():
        manifest[str(p.relative_to(source))] = hashlib.sha256(p.read_bytes()).hexdigest()
(bundle / 'BUILD.json').write_text(json.dumps(dict(version=version, base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(), includes_working_tree_changes=True, published=False, readonly=False, sources=manifest), indent=2) + '\n')
(bundle / 'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(bundle.iterdir()) if p.is_file()))
dist = root / 'dist'
dist.mkdir(exist_ok=True)
for p in packages:
    shutil.copy2(p, dist / p.name)
archive = dist / (name + '.zip')
if archive.exists():
    raise RuntimeError('refusing to replace an existing test build')
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
    for p in sorted(bundle.iterdir()):
        z.write(p, arcname=name+'/'+p.name)
(archive.with_suffix('.zip.sha256')).write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
(root / 'dist/latest-windows-test.json').write_text(json.dumps(dict(version=version, archive=archive.name, wheel=wheel.name, staging=str(work)), indent=2)+'\n')
print('BUNDLE='+str(archive))
print('STAGING='+str(work))
