#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
PID_FILE="${PROJECT_ROOT}/.report-ui.pid"
SERVER_SCRIPT="${PROJECT_ROOT}/scripts/serve_report_ui.py"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "未找到 python3，请先安装 Python 3.10 或更高版本。" >&2
    exit 1
  fi

  echo "首次启动：正在创建项目虚拟环境并安装依赖..."
  python3 -m venv "${PROJECT_ROOT}/.venv"
  "${PYTHON_BIN}" -m pip install -e "${PROJECT_ROOT}"
fi

OLD_PID=""
if [[ -f "${PID_FILE}" ]]; then
  IFS= read -r OLD_PID < "${PID_FILE}" || true
fi

if [[ "${OLD_PID}" =~ ^[0-9]+$ ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
  OLD_COMMAND="$(ps -p "${OLD_PID}" -o command= 2>/dev/null || true)"

  if [[ "${OLD_COMMAND}" == *"${SERVER_SCRIPT}"* ]]; then
    echo "正在停止旧服务（PID ${OLD_PID}）..."
    kill -TERM "${OLD_PID}"

    for ((attempt = 0; attempt < 50; attempt++)); do
      if ! kill -0 "${OLD_PID}" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done

    if kill -0 "${OLD_PID}" 2>/dev/null; then
      echo "旧服务未及时退出，正在强制停止（PID ${OLD_PID}）..."
      kill -KILL "${OLD_PID}"
    fi
  else
    echo "忽略已失效的 PID 文件：PID ${OLD_PID} 不属于本项目服务。" >&2
  fi
fi

printf '%s\n' "$$" > "${PID_FILE}"
echo "正在启动报告服务（默认地址：http://127.0.0.1:8765）"

exec "${PYTHON_BIN}" "${SERVER_SCRIPT}" \
  --project-root "${PROJECT_ROOT}" \
  --default-codex-model gpt-5.6-sol \
  --default-opencode-model deepseek/deepseek-v4-flash \
  "$@"
