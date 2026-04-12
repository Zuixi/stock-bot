服务构建指南：

## 前后端命令说明
Backend:
- 运行命令为：
```bash
cd backend && uvicorn main:app --reload
```
- 构建命令：
```bash
cd backend && uvicorn main:app --build
```

Frontend:
- 运行命令为：
```bash
cd frontend && npm run dev
```
- 构建命令：
```bash
cd frontend && npm run build
```

## 容器部署命令
TODO


## 其他命令

### Linting and Formatting
```bash
# Run linter (configured in pyproject.toml: line-length=100)
ruff check src/

# Auto-fix lint issues
ruff check --fix src/

# Type checking
mypy src/
```

### Testing
```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/path/to/test_file.py
```

### CLI Usage
```bash
# Fetch stock universe from SSE
stock-bot universe fetch --exchange sse --stock-type 1

# Fetch stock universe from BSE (Beijing Stock Exchange)
stock-bot universe fetch --exchange bse

# Fetch stock universe from SZSE (Shenzhen Stock Exchange)
stock-bot universe fetch --exchange sze

# List available snapshots
stock-bot universe list

# For development with local module
python -m src.cli.universe fetch --exchange sse
python -m src.cli.universe fetch --exchange bse
python -m src.cli.universe fetch --exchange sze
```
