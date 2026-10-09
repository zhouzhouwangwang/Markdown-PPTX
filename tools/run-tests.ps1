# 加载 test.env 后运行测试套件 —— 路径依赖的唯一入口。
# 用法: pwsh tools/run-tests.ps1 [-UnitsOnly | -E2eOnly]
param([switch]$UnitsOnly, [switch]$E2eOnly)

$root = Split-Path -Parent $PSScriptRoot   # pptx-site-theme/
Set-Location $root

Get-Content -Encoding UTF8 (Join-Path $root "test.env") | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        $parts = $line -split "=", 2
        if ($parts.Length -eq 2) {
            $k, $v = $parts[0].Trim(), $parts[1].Trim()
            # 只有路径键需要相对→绝对解析；其余（如 PPTSVC_E2E=1）原样透传
            if ($k -in @("PLAYWRIGHT_BROWSERS_PATH", "PROBE_PLAYWRIGHT")) {
                if (-not [System.IO.Path]::IsPathRooted($v)) {
                    $v = Join-Path $root $v
                }
                if (Test-Path $v) { $v = (Resolve-Path $v).Path }
            }
            Set-Item -Path "Env:$k" -Value $v
        }
    }
}

if (-not $E2eOnly) {
    Write-Host "== unit tests ==" -ForegroundColor Cyan
    python -m pytest tests/test_units.py -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (-not $UnitsOnly) {
    Write-Host "== e2e tests ==" -ForegroundColor Cyan
    python -m pytest tests/test_api.py -q -p no:cacheprovider
    exit $LASTEXITCODE
}
