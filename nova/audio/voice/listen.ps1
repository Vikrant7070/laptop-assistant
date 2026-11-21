
param([string]$Mode = "dictation")
Add-Type -AssemblyName System.Speech
$culture = New-Object System.Globalization.CultureInfo("en-US")
$r = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
try { $r.SetInputToDefaultAudioDevice() } catch { [Console]::Out.WriteLine("ERROR:no microphone"); exit 1 }
$c = New-Object System.Speech.Recognition.Choices
$c.Add([string[]]@("hey nova", "hi nova", "hello nova", "okay nova", "ok nova", "nova", "hey noah", "hey nover",
  "nova wake up", "wake up nova", "hey nova wake up", "nova are you there",
  "nova go to sleep", "go to sleep nova", "nova sleep", "nova stop listening", "good night nova"))
$gb = New-Object System.Speech.Recognition.GrammarBuilder
$gb.Culture = $culture
$gb.Append($c)
$wake = New-Object System.Speech.Recognition.Grammar($gb)
$wake.Name = "wake"
$dict = New-Object System.Speech.Recognition.DictationGrammar
$dict.Name = "dict"
# Common requests as exact phrases: the recognizer is far more accurate on these than on free dictation.
$cc = New-Object System.Speech.Recognition.Choices
$cc.Add([string[]](Get-Content -Path "$PSScriptRoot\phrases.txt" -Encoding UTF8))
$cgb = New-Object System.Speech.Recognition.GrammarBuilder
$cgb.Culture = $culture
$cgb.Append($cc)
$cmd = New-Object System.Speech.Recognition.Grammar($cgb)
$cmd.Name = "cmd"
$cmd.Weight = 1.0
$dict.Weight = 0.5
# Modern Windows recognizer (much more accurate) for the request after the wake word. Needs
# Settings > Privacy & security > Speech > "Online speech recognition" switched on; otherwise we fall back.
$rt = $null
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $null = [Windows.Media.SpeechRecognition.SpeechRecognizer, Windows.Media.SpeechRecognition, ContentType = WindowsRuntime]
  $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
      $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  function Await($op, [Type]$t, $ms = 15000) {
    $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op))
    if (-not $task.Wait($ms)) { try { $op.Cancel() } catch { }; throw "timed out after $($ms / 1000) s" }
    $task.Result
  }
  $rt = New-Object Windows.Media.SpeechRecognition.SpeechRecognizer
  $rt.Timeouts.InitialSilenceTimeout = [TimeSpan]::FromSeconds(6)
  $rt.Timeouts.EndSilenceTimeout = [TimeSpan]::FromMilliseconds(1000)
  $null = Await ($rt.CompileConstraintsAsync()) ([Windows.Media.SpeechRecognition.SpeechRecognitionCompilationResult])
} catch { $rt = $null }
function OnlineOK { ((Get-ItemProperty "HKCU:\Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy" -ErrorAction SilentlyContinue).HasAccepted -eq 1) }
$r.InitialSilenceTimeout = [TimeSpan]::FromSeconds(6)
$r.EndSilenceTimeout = [TimeSpan]::FromMilliseconds(900)
# A wake-only grammar forces any sound onto the nearest phrase ("nova"), so a hum or a chair could pass the old 0.3
# limit; 0.5 asks for her name said clearly (anything between shows as "almost heard" in her status notes).
$r.LoadGrammar($wake)
[Console]::Out.WriteLine("READY"); [Console]::Out.Flush()
while ($true) {
  $res = $null
  try { $res = $r.Recognize([TimeSpan]::FromSeconds(20)) } catch { }
  if ($res -and $res.Grammar.Name -eq "wake" -and $res.Confidence -lt 0.5) {
    [Console]::Out.WriteLine("LOW:" + [math]::Round($res.Confidence, 2) + "|" + $res.Text); [Console]::Out.Flush()
  }
  if ($res -and $res.Grammar.Name -eq "wake" -and $res.Confidence -ge 0.5) {
    if ($res.Text -match "sleep|stop listening|good night") { [Console]::Out.WriteLine("SLEEP"); [Console]::Out.Flush(); continue }
    if ($res.Text -match "wake up|are you there") { [Console]::Out.WriteLine("WAKEUP"); [Console]::Out.Flush(); continue }
    [Console]::Out.WriteLine("WAKE"); [Console]::Out.Flush()
    # In the desktop app, Nova hands the request to Windows voice typing (accurate) - nothing more to do here.
    if ($Mode -eq "typing") { continue }
    if ($rt -and (OnlineOK)) {
      $done = $false
      try {
        $res = Await ($rt.RecognizeAsync()) ([Windows.Media.SpeechRecognition.SpeechRecognitionResult])
        if ($res.Status -eq "Success" -and $res.Text) {
          $conf = @{ "High" = 0.95; "Medium" = 0.8; "Low" = 0.5; "Rejected" = 0.1 }[[string]$res.Confidence]
          [Console]::Out.WriteLine("TEXT:rt|" + $conf + "|" + $res.Text)
        } else { [Console]::Out.WriteLine("INFO:accurate recognizer heard no words (status " + $res.Status + ")"); [Console]::Out.WriteLine("NOTHING") }
        $done = $true
      } catch {
        $m = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
        [Console]::Out.WriteLine("INFO:accurate recognizer failed (" + $m.Trim() + ") - using basic one")
      }
      [Console]::Out.Flush()
      if ($done) { continue }
    } else {
      [Console]::Out.WriteLine("INFO:online-off"); [Console]::Out.Flush()
    }
    $r.UnloadAllGrammars(); $r.LoadGrammar($cmd); $r.LoadGrammar($dict)
    $said = $null
    try { $said = $r.Recognize([TimeSpan]::FromSeconds(12)) } catch { }
    if ($said -and $said.Text) {
      $conf = [math]::Round($said.Confidence, 2)
      [Console]::Out.WriteLine("TEXT:" + $said.Grammar.Name + "|" + $conf + "|" + $said.Text)
    } else { [Console]::Out.WriteLine("NOTHING") }
    [Console]::Out.Flush()
    $r.UnloadAllGrammars(); $r.LoadGrammar($wake)
  }
}
