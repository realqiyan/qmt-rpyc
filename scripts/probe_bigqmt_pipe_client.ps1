param([string]$PipeName = "qmt_rpyc_probe_v1")
$ErrorActionPreference = "Stop"

function Open-ProbePipe {
    $pipe = [System.IO.Pipes.NamedPipeClientStream]::new(
        ".", $PipeName, [System.IO.Pipes.PipeDirection]::InOut,
        [System.IO.Pipes.PipeOptions]::Asynchronous)
    try {
        $pipe.Connect(3000)
        $pipe.ReadMode = [System.IO.Pipes.PipeTransmissionMode]::Message
        return $pipe
    } catch { $pipe.Dispose(); throw }
}

function Exchange-Probe($Pipe, [string]$Wire, [int]$TimeoutMs = 5000) {
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Wire)
    $write = $Pipe.WriteAsync($bytes, 0, $bytes.Length)
    if (-not $write.Wait($TimeoutMs)) { throw [TimeoutException]::new("write timeout") }
    $output = [System.IO.MemoryStream]::new()
    try {
        do {
            $remaining = $TimeoutMs - [int]$clock.ElapsedMilliseconds
            if ($remaining -le 0) { throw [TimeoutException]::new("read timeout") }
            $buffer = New-Object byte[] 8192
            $read = $Pipe.ReadAsync($buffer, 0, $buffer.Length)
            if (-not $read.Wait($remaining)) { throw [TimeoutException]::new("read timeout") }
            $count = $read.Result
            if ($count -eq 0) { throw "peer closed" }
            $output.Write($buffer, 0, $count)
            if ($output.Length -gt 262144) { throw "oversized response" }
        } while (-not $Pipe.IsMessageComplete)
        return ([System.Text.Encoding]::UTF8.GetString($output.ToArray()) | ConvertFrom-Json)
    } finally { $output.Dispose() }
}

function Request-Wire([string]$Id, [string]$Payload, [bool]$Delay = $false) {
    return (@{id=$Id; payload=$Payload; delay=$Delay} | ConvertTo-Json -Compress)
}

function Assert-Echo($Pipe, [string]$Payload) {
    $id = [Guid]::NewGuid().ToString("N")
    $result = Exchange-Probe $Pipe (Request-Wire $id $Payload)
    if ($result.status -ne "ok" -or $result.id -cne $id -or $result.payload -cne $Payload) {
        throw "response correlation or payload mismatch"
    }
}

$passed = @()
try {
    $pipe = Open-ProbePipe
    try {
        for ($i = 0; $i -lt 20; $i++) { Assert-Echo $pipe "echo-$i" }
        $passed += "20 ordered roundtrips"
        Assert-Echo $pipe (([string][char]0x4e2d) * 30000)
        $passed += "90000-byte UTF-8 payload"
        $bad = Exchange-Probe $pipe '{'
        if ($bad.status -ne "invalid_request") { throw "malformed request not rejected" }
        Assert-Echo $pipe "after-invalid-request"
        $passed += "malformed request then valid request"
    } finally { $pipe.Dispose() }

    for ($i = 0; $i -lt 8; $i++) {
        $pipe = Open-ProbePipe
        try { Assert-Echo $pipe "reconnect-$i" } finally { $pipe.Dispose() }
    }
    $passed += "8 fresh connections"

    $pipes = @()
    try {
        for ($i = 0; $i -lt 4; $i++) { $pipes += (Open-ProbePipe) }
        for ($i = 0; $i -lt 4; $i++) { Assert-Echo $pipes[$i] "connection-$i" }
        $passed += "4 simultaneous open connections"
    } finally { foreach ($item in $pipes) { $item.Dispose() } }

    $slowPipe = Open-ProbePipe
    try {
        $wire = Request-Wire "slow-reader" (([string][char]0x4e2d) * 30000)
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($wire)
        $write = $slowPipe.WriteAsync($bytes, 0, $bytes.Length)
        if (-not $write.Wait(5000)) { throw "slow-reader request write timed out" }
        # Do not read the large response; another connection must progress.
        $pipe = Open-ProbePipe
        try { Assert-Echo $pipe "while-peer-not-reading" } finally { $pipe.Dispose() }
        Start-Sleep -Seconds 6
        $passed += "other connection progresses while peer does not read"
    } finally { $slowPipe.Dispose() }

    $pipe = Open-ProbePipe
    try {
        $timedOut = $false
        try { $null = Exchange-Probe $pipe (Request-Wire "delayed" "abandoned" $true) 200 }
        catch [TimeoutException] { $timedOut = $true }
        if (-not $timedOut) { throw "expected delayed-response timeout" }
    } finally { $pipe.Dispose() }
    $pipe = Open-ProbePipe
    try { Assert-Echo $pipe "fresh-after-timeout" } finally { $pipe.Dispose() }
    $passed += "timeout then fresh connection without replay"

    @{status="passed"; checks=$passed; production_ready=$false} | ConvertTo-Json -Depth 4
} catch {
    @{status="failed"; checks_passed=$passed; error_type=$_.Exception.GetType().Name;
      message=$_.Exception.Message} | ConvertTo-Json -Depth 4
    exit 1
}
