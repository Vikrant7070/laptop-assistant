
# Background transcriber: Windows' modern speech recognizer, no window. Reads "LISTEN" lines, writes one result each.
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechRecognition.SpeechRecognizer, Windows.Media.SpeechRecognition, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t, $ms) {
  $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op))
  if (-not $task.Wait($ms)) { try { $op.Cancel() } catch { }; throw "timed out" }
  $task.Result
}
try {
  $rt = New-Object Windows.Media.SpeechRecognition.SpeechRecognizer
  $rt.Timeouts.InitialSilenceTimeout = [TimeSpan]::FromSeconds(8)
  $rt.Timeouts.EndSilenceTimeout = [TimeSpan]::FromMilliseconds(1100)
  $c = Await ($rt.CompileConstraintsAsync()) ([Windows.Media.SpeechRecognition.SpeechRecognitionCompilationResult]) 20000
  [Console]::Out.WriteLine("READY:" + $c.Status); [Console]::Out.Flush()
} catch { [Console]::Out.WriteLine("ERROR:" + $_.Exception.Message); [Console]::Out.Flush(); exit 1 }
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($line -eq $null) { break }
  if ($line -ne "LISTEN") { continue }
  try {
    $res = Await ($rt.RecognizeAsync()) ([Windows.Media.SpeechRecognition.SpeechRecognitionResult]) 25000
    [Console]::Out.WriteLine("RESULT:" + $res.Status + "|" + $res.Confidence + "|" + $res.Text)
  } catch {
    $m = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
    [Console]::Out.WriteLine("ERROR:" + ($m -replace "\s+", " ").Trim())
  }
  [Console]::Out.Flush()
}
