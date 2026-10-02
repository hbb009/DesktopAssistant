param(
  [Parameter(Mandatory=$true)][string]$InternalDir
)
# Post-build cleanup for already-packed _internal (same priority cuts).
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $InternalDir)) { throw "Not found: $InternalDir" }
function MB($p) {
  if (-not (Test-Path -LiteralPath $p)) { return 0 }
  [math]::Round(((Get-ChildItem -LiteralPath $p -Recurse -File -EA SilentlyContinue | Measure-Object Length -Sum).Sum)/1MB,1)
}
$before = MB $InternalDir
$dirs = @('pyarrow','modelscope','matplotlib','matplotlib.libs','mpl_toolkits','sklearn','skimage','Cython','llvmlite','llvmlite.libs','numba','tensorboard','tensorboard_data_server')
foreach ($d in $dirs) {
  $p = Join-Path $InternalDir $d
  if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force; Write-Host "removed $d" }
}
Get-ChildItem -LiteralPath $InternalDir -Force | Where-Object {
  $_.Name -like 'matplotlib-*.dist-info' -or $_.Name -like 'scikit_*-*.dist-info' -or
  $_.Name -like 'pyarrow-*.dist-info' -or $_.Name -like 'modelscope-*.dist-info' -or
  $_.Name -like 'llvmlite-*.dist-info' -or $_.Name -like 'numba-*.dist-info' -or
  $_.Name -like 'Cython-*.dist-info' -or $_.Name -like 'tensorboard*.dist-info'
} | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force; Write-Host "removed $($_.Name)" }
$cv2 = Join-Path $InternalDir 'cv2'
foreach ($n in @('opencv_videoio_ffmpeg4100_64.dll','opencv_videoio_ffmpeg4110_64.dll')) {
  $f = Join-Path $cv2 $n
  if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force; Write-Host "removed cv2/$n" }
}
$after = MB $InternalDir
Write-Host "BEFORE=$before MB AFTER=$after MB SAVED=$([math]::Round($before-$after,1)) MB"
