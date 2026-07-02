# MyStock Windows 绿色版打包说明

把项目打包成一个**免安装绿色文件夹**，分发给 Windows 用户，双击即可使用。
用户在 Web 看板的「⚙️ 数据管理」里完成行情同步和本项目数据刷新，无需命令行。

## 目录结构

仓库本身就是包根目录（`cli/core/ui/sql/config` 直接位于根下），
绿色文件夹直接把仓库内容平铺，不要再套一层 `rps\`。

```
MyStock\
├── python\              嵌入式 Python 运行时（含依赖）
│   └── python.exe
├── cli\                 本项目代码（命令入口）
├── core\
├── ui\
├── sql\
├── config\
├── tdx2db.exe           行情数据同步程序（你提供）
├── data\                数据库目录（初始化后生成 tdx.db）
├── 启动.bat             一键启动脚本（放在根目录）
└── README_打包说明.md
```

## 打包步骤

### 1. 准备嵌入式 Python

1. 下载 [Windows embeddable package](https://www.python.org/downloads/windows/)
   （选 3.11/3.12 的 `embeddable package (64-bit)`），解压到 `python\`。
2. 启用 site-packages：编辑 `python\pythonXX._pth`，取消 `#import site` 的注释。
3. 装 pip：下载 `get-pip.py` 放到 `python\`，运行
   ```
   python\python.exe python\get-pip.py
   ```
4. 安装依赖（在仓库根目录有 pyproject.toml）：
   ```
   python\python.exe -m pip install duckdb pandas pyyaml click streamlit plotly matplotlib akshare
   ```

### 2. 拷贝项目代码

把仓库的 `cli/ core/ ui/ sql/ config/` 直接拷进绿色文件夹**根目录**。
> 运行入口从仓库根目录执行：`python -m cli.run_daily`、
> `python -m streamlit run ui\streamlit_app.py`，
> 依赖 cwd 在仓库根（启动.bat 已 `cd /d %~dp0` 处理）。
> 启动.bat 放在根目录，与 cli/ui 同级。

### 3. 放入 tdx2db.exe

把 `tdx2db.exe` 放到绿色文件夹**根目录**（与 `启动.bat` 同级）。
数据管理模块默认从这里找它，也可在 UI 里改路径。

### 4. 分发

整个 `MyStock\` 文件夹压缩发给用户，解压双击 `启动.bat` 即可。

## 用户使用流程

1. 双击 `启动.bat`，浏览器打开看板。
2. 进入「⚙️ 数据管理」：
   - **路径配置**：确认 tdx2db.exe 路径、数据库路径，填写通达信 `vipdoc` 目录。
   - **首次**：点「tdx2db 初始化」→ 等待完成 → 点「本项目初始化历史」。
   - **日常**：点「tdx2db 日常更新」→ 点「本项目日常刷新」。
   - 这两步分别对应 `tdx2db init` 和 `python -m cli.run_daily --init-history`。
3. 切到「三线红榜单 / 自选筛选 / 市场宽度」查看结果。

## 注意

- DuckDB 不支持多进程并发写。数据管理在运行 tdx2db / 刷新前会自动释放看板的只读连接；
  运行期间请勿在其他模块频繁查询。
- tdx2db 命令：
  - 初始化：`tdx2db init --dburi duckdb://<db> --dayfiledir <vipdoc>`
  - 日常更新：`tdx2db cron --dburi duckdb://<db>`
