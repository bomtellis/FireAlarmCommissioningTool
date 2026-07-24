param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string] $Path,

    [switch] $IncludePoints,

    [string] $JsonOutput
)

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.IO.Compression.FileSystem

if (-not ('NcfBinaryScanner' -as [type])) {
    Add-Type -TypeDefinition @"
using System;

public static class NcfBinaryScanner
{
    private const int PointRecordSize = 224;

    private static bool IsPointRecord(byte[] data, int offset)
    {
        if (offset < 0 || offset + PointRecordSize > data.Length)
            return false;

        if (BitConverter.ToInt32(data, offset + 8) != 2)
            return false;

        int channel = data[offset + 16];
        int address = data[offset + 17];
        int loop = data[offset + 18];
        int textLength = data[offset + 20];

        if (channel < 1 || channel > 16 ||
            address < 1 || address > 126 ||
            loop < 1 || loop > 200 ||
            textLength > 27)
            return false;

        for (int i = 0; i < textLength; i++)
        {
            byte value = data[offset + 21 + i];
            if (value < 32 || value > 126)
                return false;
        }

        return true;
    }

    public static int[] FindLongestPointTable(byte[] data)
    {
        int bestOffset = -1;
        int bestCount = 0;

        for (int offset = 0; offset + PointRecordSize <= data.Length; offset++)
        {
            if (!IsPointRecord(data, offset))
                continue;

            int count = 0;
            while (IsPointRecord(data, offset + (count * PointRecordSize)))
                count++;

            if (count > bestCount)
            {
                bestOffset = offset;
                bestCount = count;
            }

            offset += (count * PointRecordSize) - 1;
        }

        return new[] { bestOffset, bestCount };
    }
}
"@
}

function Read-ZipEntryBytes {
    param([System.IO.Compression.ZipArchiveEntry] $Entry)

    $stream = $Entry.Open()
    $memory = [System.IO.MemoryStream]::new()
    try {
        $stream.CopyTo($memory)
        # Prevent PowerShell from unrolling the byte array into the pipeline.
        return ,$memory.ToArray()
    }
    finally {
        $stream.Dispose()
        $memory.Dispose()
    }
}

function Read-Ascii {
    param(
        [byte[]] $Data,
        [int] $Offset,
        [int] $Length
    )

    if ($Length -eq 0) {
        return ''
    }

    return [System.Text.Encoding]::ASCII.GetString($Data, $Offset, $Length)
}

function Get-ObservedDeviceType {
    param([int] $ProductCode)

    # These mappings were validated against zones.csv for node 52.
    switch ($ProductCode) {
        6  { return 'Call Point' }
        33 { return 'Relay' }
        38 { return 'Relay' }
        40 { return 'Sounder' }
        45 { return 'Optical Smoke' }
        46 { return 'Heat Detector' }
        default { return $null }
    }
}

$resolvedPath = (Resolve-Path -LiteralPath $Path).Path
$fileInfo = Get-Item -LiteralPath $resolvedPath
$zip = [System.IO.Compression.ZipFile]::OpenRead($resolvedPath)

try {
    $entries = @(
        $zip.Entries | ForEach-Object {
            [ordered]@{
                name = $_.FullName
                uncompressedBytes = $_.Length
                compressedBytes = $_.CompressedLength
            }
        }
    )

    $siteEntry = $zip.GetEntry('SITE')
    if ($null -eq $siteEntry) {
        throw "The archive does not contain the expected SITE entry."
    }

    $siteData = Read-ZipEntryBytes -Entry $siteEntry
    $siteText = [System.Text.Encoding]::ASCII.GetString($siteData)
    $versions = @(
        [regex]::Matches($siteText, '\d+\.\d+') |
            ForEach-Object Value |
            Select-Object -Unique
    )

    $nodes = [System.Collections.Generic.List[object]]::new()
    $siteRecordSize = 112

    for ($index = 0; ; $index++) {
        $siteRecordOffset = 112 + ($index * $siteRecordSize)
        if ($siteRecordOffset + $siteRecordSize -gt $siteData.Length) {
            break
        }

        # Observed node record marker and length-prefixed 32-byte ASCII name.
        if ($siteData[$siteRecordOffset + 8] -ne 18) {
            break
        }

        $nameLength = $siteData[$siteRecordOffset + 9]
        if ($nameLength -gt 32) {
            break
        }

        $panelName = Read-Ascii -Data $siteData -Offset ($siteRecordOffset + 10) -Length $nameLength
        $nodeNumber = $siteData[$siteRecordOffset + 44]

        $nodes.Add([ordered]@{
            node = $nodeNumber
            panel = $panelName
            siteRecordOffset = $siteRecordOffset
        })
    }

    $pcfEntries = @{}
    foreach ($entry in $zip.Entries) {
        if ($entry.FullName.EndsWith('.pcf', [System.StringComparison]::OrdinalIgnoreCase)) {
            $baseName = [System.IO.Path]::GetFileNameWithoutExtension($entry.Name)
            $pcfEntries[$baseName] = $entry
        }
    }

    $allPoints = [System.Collections.Generic.List[object]]::new()
    $totalSubRecords = 0
    $totalPhysicalDevices = 0

    foreach ($node in $nodes) {
        if (-not $pcfEntries.ContainsKey($node.panel)) {
            $node.pcfBytes = $null
            $node.pointTableOffset = $null
            $node.pointSubRecords = 0
            $node.physicalDevices = 0
            $node.loops = @()
            continue
        }

        $pcfEntry = $pcfEntries[$node.panel]
        $pcfData = Read-ZipEntryBytes -Entry $pcfEntry
        $table = [NcfBinaryScanner]::FindLongestPointTable($pcfData)
        $tableOffset = $table[0]
        $subRecordCount = $table[1]
        $devices = [ordered]@{}

        for ($recordIndex = 0; $recordIndex -lt $subRecordCount; $recordIndex++) {
            $offset = $tableOffset + ($recordIndex * 224)
            $channel = [int] $pcfData[$offset + 16]
            $address = [int] $pcfData[$offset + 17]
            $loop = [int] $pcfData[$offset + 18]
            $textLength = [int] $pcfData[$offset + 20]
            $text = (Read-Ascii -Data $pcfData -Offset ($offset + 21) -Length $textLength).TrimEnd()
            $zone = [BitConverter]::ToInt32($pcfData, $offset + 48)
            $productCode = [BitConverter]::ToInt32($pcfData, $offset + 12)
            $key = "$loop/$address"

            $subPoint = [ordered]@{
                channel = $channel
                text = $text
                zone = $zone
                productCode = $productCode
                observedType = Get-ObservedDeviceType -ProductCode $productCode
                recordOffset = $offset
            }

            if (-not $devices.Contains($key)) {
                $devices[$key] = [ordered]@{
                    node = $node.node
                    panel = $node.panel
                    loop = $loop
                    address = $address
                    productCode = $productCode
                    observedType = Get-ObservedDeviceType -ProductCode $productCode
                    text = $text
                    zone = $zone
                    channels = [System.Collections.Generic.List[object]]::new()
                }
            }

            $devices[$key].channels.Add($subPoint)
        }

        $loops = @(
            $devices.Values |
                ForEach-Object loop |
                Sort-Object -Unique
        )

        $node.pcfBytes = $pcfEntry.Length
        $node.pointTableOffset = if ($tableOffset -ge 0) { $tableOffset } else { $null }
        $node.pointSubRecords = $subRecordCount
        $node.physicalDevices = $devices.Count
        $node.loops = $loops

        $totalSubRecords += $subRecordCount
        $totalPhysicalDevices += $devices.Count

        if ($IncludePoints) {
            foreach ($device in $devices.Values) {
                $allPoints.Add($device)
            }
        }
    }

    $result = [ordered]@{
        source = $resolvedPath
        bytes = $fileInfo.Length
        format = 'ZIP container with SITE, AppDefaults.ini, PCF panel files, and TXT/RTF notes'
        versionsObserved = $versions
        archiveEntries = $entries.Count
        nodeCount = $nodes.Count
        pcfCount = $pcfEntries.Count
        totalPhysicalDevices = $totalPhysicalDevices
        totalPointSubRecords = $totalSubRecords
        nodes = $nodes
        entries = $entries
    }

    if ($IncludePoints) {
        $result.points = $allPoints
    }

    $json = $result | ConvertTo-Json -Depth 12

    if ($JsonOutput) {
        $outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($JsonOutput)
        [System.IO.File]::WriteAllText($outputPath, $json, [System.Text.UTF8Encoding]::new($false))
        Write-Output "Wrote $outputPath"
    }
    else {
        Write-Output $json
    }
}
finally {
    $zip.Dispose()
}
