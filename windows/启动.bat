@echo off
REM ============================================================
REM  MyStock 一键启动脚本（Windows 绿色版）
REM
REM  目录结构（绿色文件夹）：
REM    MyStock\
REM      python\              <- 嵌入式 Python 运行时（python.exe 在此）
REM      rps\                <- 本项目代码（cli/core/ui/sql/config）
REM      tdx2db.exe          <- 行情数据同步程序
REM      data\tdx.db         <- DuckDB 数据库文件（首次初始化后生成）
REM      启动.bat            <- 本脚本
REM
REM  双击本脚本即可启动 Web 看板（浏览器自动打开 http://localhost:8501）
REM ============================================================

setlocal
cd /d "%~dp0"

set PY=python\python.exe
set DB=%~dp0data\tdx.db

REM 若嵌入式 python 不存在，回退到系统 python
if not exist "%PY%" set PY=python

echo 正在启动 MyStock 看板...
"%PY%" -m streamlit run rps\ui\streamlit_app.py -- --db "%DB%"

pause
endlocal
