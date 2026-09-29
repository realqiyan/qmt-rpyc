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
(source / 'scripts').mkdir()
shutil.copy2(root / 'scripts/build_bigqmt_strategy.py', source / 'scripts/build_bigqmt_strategy.py')
subprocess.run([str(python), str(source / 'scripts/build_bigqmt_strategy.py'), '--output', str(bundle / 'bigqmt_strategy.py')], check=True)
shutil.copy2(root / 'scripts/check_bigqmt_bridge.py', bundle / 'check_bigqmt_bridge.py')
shutil.copy2(root / 'LICENSE', bundle / 'LICENSE')
for filename in ('verify-install.py', '.env.example'):
    shutil.copy2(root / filename, bundle / filename)
shutil.copy2(root / 'scripts/verify_persistent_data.py', bundle / 'verify_persistent_data.py')
shutil.copy2(root / 'docs/design/persistent-cache.md', bundle / 'persistent-cache.md')

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
".venv\\Scripts\\python.exe" verify-install.py --expected-version "BUILD_VERSION"
if errorlevel 1 goto failed
echo Installation complete. See README.txt, then run check-readonly.bat.
pause
exit /b 0
:failed
echo Installation failed. Review the error above.
pause
exit /b 1
'''.replace('WHEEL_NAME', wheel.name).replace('BUILD_VERSION', version))

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
set "QMT_RPYC_CACHE_PATH=%~dp0data.sqlite3"
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

readme = f'''BigQMT Windows 数据存储层测试包
构建版本：{version}

当前未提交工作区的独立测试构建，未发布；契约与策略协议均为 8。
不包含真实配置、认证密钥、账户或日志。需联网从 PyPI 安装依赖。

一、安装与启动
1. 解压到独立目录；安装 Windows 64 位 Python 3.11 或 3.10（包含 py 启动器）。
   QMT 内置 Python 3.6.8 无需安装依赖或修改。
2. 运行 install.bat，创建独立 .venv，验证 wheel、策略版本和策略指纹一致。
3. 停止旧服务和旧桥策略，保留旧安装目录与策略作为回退。
   将已有 config.env 复制到本目录，保留监听地址、认证密钥与管道配置。
   没有现成配置时，可参考 .env.example；不要把配置发回或公开。
4. 将 bigqmt_strategy.py 按 GBK 编码导入 QMT 并运行。
   策略 ready 日志中的 version 应为 {version}。
   默认管道 qmt_rpyc_bridge_v1；自定义管道须同时匹配策略、服务与检查参数。
5. 运行 check-readonly.bat；自定义管道使用 --pipe 管道名。
   默认检查行情、日 K、证券资料、日历和期权，不下单、不撤单、不显式下载。
   --extended 额外检查全市场快照与指数权重，可能较慢。
6. 运行 start-debug-server.bat 进行数据核对；普通启动用 start-server.bat。
   调试入口需共享密钥认证。策略和服务具备交易能力；本包检查不调用交易。
   测试客户端也需安装随附 wheel，不能沿用旧契约客户端。

二、数据存储验收
启动脚本固定使用本解压目录 data.sqlite3，避免复用原环境数据库。
缓存策略使用配置中的 QMT_RPYC_CACHE_POLICIES（未设置则使用默认策略）。
选定一个已结束的历史日期区间，以相同证券和参数重复读取日 K：
- 首次读取建立缓存；第二次应复用，比较数据一致性和耗时。
- 重启服务后再次读取，确认数据持久化且结果一致。
- 使用 refresh=True 读取，确认从源更新，后续读取结果一致。
- 核对 health 中的持久化状态；异常时记录错误，不发送密钥或账户信息。
复权核对脚本 verify_persistent_data.py 使用客户端 debug profile；先运行 --help
查看证券与日期参数，再在已配置 profile 的客户端环境执行。它验证源数据与
本地推导的一致性，不替代以上服务缓存命中验收。

三、已知边界
财务数据缺少完整性证明时仍回源；xtquant 分红和本地复权仍保守回源。
不能可靠推导的停牌填充、特殊分红等场景仍由原数据源处理。
当日 K 线不持久化。完整设计与边界见 persistent-cache.md。
Linux 自动化验证不能代替本次 Windows/QMT 管道、数据与稳定性验收。

四、回退与文件
停止测试服务及策略后，恢复旧策略并启动旧安装目录即可。
测试数据库留在本目录；服务停止后可删除整个目录清理测试环境。
BUILD.json 记录版本和源码摘要，SHA256SUMS.txt 记录包内文件校验值。
安装脚本不会创建或覆盖 config.env。本包不是完整离线依赖包。
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
(root / 'dist/latest-windows-test.json').write_text(json.dumps(dict(version=version, archive=archive.name, wheel=wheel.name), indent=2)+'\n')
print('BUNDLE='+str(archive))
print('STAGING='+str(work))
