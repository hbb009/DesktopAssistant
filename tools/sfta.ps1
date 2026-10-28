<#
.SYNOPSIS
    Set File / Protocol Type Association for Windows 10 / 11 (UserChoice + UserChoiceLatest).

.DESCRIPTION
    Programmatically sets the default application for a file extension or a URL protocol.

    Starting with Windows 11 (feature "AppDefaultHashRotation", GA on 24H2/25H2) Microsoft
    added a second, tamper-proof association key next to the classic UserChoice:

        HKCU\...\FileExts\<.ext>\UserChoiceLatest        (value: Hash, subkey ProgId\ProgId)
        HKCU\...\UrlAssociations\<proto>\UserChoiceLatest

    Once the per-user flag
        HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\SystemProtectedUserData\<SID>\AnyoneRead\AppDefaults : HashVersion = 1
    is set, Explorer IGNORES UserChoice and reads ONLY UserChoiceLatest. The old PS-SFTA
    hash therefore no longer changes the default - the association silently reverts.

    UserChoiceLatest uses a completely different, salted hash. This script reimplements it
    1:1 (verified against Windows' own Windows.Internal.OpenWithHost.dll and against every
    live association on the test machine) and writes BOTH keys so it works whether or not
    the new scheme is active.

    See: https://github.com/DanysysTeam/PS-SFTA/issues/37
         https://kolbi.cz/blog/2025/04/20/userchoicelatest-microsofts-new-protection-for-file-type-associations/

.NOTES
    Original PS-SFTA (legacy UserChoice hash, Get/Set/Register/Remove-FTA):
        Danyfirex & Dany3j - Copyright (c) 2022 Danysys <danysys.com>, MIT License
        https://github.com/DanysysTeam/PS-SFTA
        Its own credits: https://bbs.pediy.com/thread-213954.htm
                         LMongrain - Hash Algorithm PureBasic Version
    UserChoiceLatest token encoder, lookup tables and mixing routines (adapted to C# below):
        cssxn - Copyright (c) 2026 cssxn, MIT License
        https://github.com/cssxn/UserChoiceLatestHash
    UserChoiceLatest approach - locating Windows' own encoder in OpenWithHost.dll by signature,
    the three salt GUIDs and the per-class field order - first published by araghon007 in
    DefaultApps (NewHash.cs): https://github.com/araghon007/DefaultApps
    That project has no license, so none of its code is included here.
    UserChoiceLatest fork: Copyright (c) 2026 ComoEstaisAmigos
    License : MIT (full notice below)

.EXAMPLE
    . .\SFTA.ps1 ; Set-FTA ChromeHTML .htm
    Set Google Chrome's ProgId as default for .htm

.EXAMPLE
    . .\SFTA.ps1 ; Set-PTA MSEdgeHTM https
    Set Edge as default https handler

.EXAMPLE
    . .\SFTA.ps1 ; Get-FTA .pdf
    Show the current default ProgId for .pdf

.EXAMPLE
    powershell -ExecutionPolicy Bypass -command "& { . .\SFTA.ps1 ; Set-FTA 'Applications\SumatraPDF.exe' '.pdf' }"

.LINK
    https://github.com/DanysysTeam/PS-SFTA
#>

<#
MIT License

Copyright (c) 2022 Danysys <danysys.com>
Copyright (c) 2026 cssxn
Copyright (c) 2026 ComoEstaisAmigos

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
#>

Set-StrictMode -Off

# Where this file lives, so messages can print a command that works from any directory.
$script:SftaScriptPath = $PSCommandPath

# HKCU roots of the per-user choices for file extensions and URL protocols.
$script:FileExtsRoot = 'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts'
$script:UrlAssocRoot = 'Software\Microsoft\Windows\Shell\Associations\UrlAssociations'

# ---------------------------------------------------------------------------
#  UserChoiceLatest hash engine (C#): a self-contained managed encoder plus a bridge to
#  Windows' own encoder. The six lookup tables (decoded from cssxn's
#  UserChoiceLatestHashTables.inc) are embedded below as one gzip+Base64 blob; no external
#  file is required.
# ---------------------------------------------------------------------------
$script:UclTablesBlob = @'
H4sIAAAAAAAACtVdWY4cxxGNqowtt6peKNg0acMEfwz4S4AI6BAC9KEL6AKCfCkDPoAP5Evwh/NjZPUyPZohBS6S4r3pmp7prsp+FZnxIiNqaZpow3iaz0v6
xcK/sshnLvoHL/+bif7zr59/+PbvL4/Hw+GwW9dlWZbeW6u11lJKvoVfYU9Dfw3yZcCfizQwn0bArFb+/Pyv93j79t0Fd3d3d/QQX7969Xyfs6qMvRmNydbe
aJV5Tmmat9bHB5wxPmp+jOv70+Xty1rXtadpGsv2tP33QdysMZ83vP/1sXhqo80Ad+/e/ven7978828v/vTVce2t5Qf9/wl9lj6AT+J+5XpxcDTOX1iXfvz3
uUWEx+/984kAoUnT9OUfvwW+hM/8Hrh87vweUBC8j18kjgNpeiienIbCxUNK17h+AUXEmG7cWJN5BISAeBxCKSQeRfcRUAPiF9OSoCzH+Hwwj6KouHEgtsA8
H5hzJHVBcRmXJ1BYXDudY/N8mI5QWJyNOdJkDTw8H9jTKS5uaDYKjHOfs2jobidOnM7VKYoMYdkqTiKxed4UUygy7mnGtucprrtZZFEa/X4t8FJo7E81W4kc
MwfSpko5B+92Sizqrh6d51B5G0QpNs6xvRwoNk6y5DlTbIwZiJllo9jY0o1xbI1iQ5IMh4/PU4Qh7CkjasbnOQ54qml4/bwehabYuBwSj27PJO7mHl3mKamY
u1cKDmaxcTIFBQezqluJb89RV8o1PE9hViulUHCIsCKMzzFdMiuha2ADY/Jp4bP3ETdPoOBIal7id/spjzscEHjm3BaKjk3lc3iZp5EUe/xwRGwC4UeSzCuA
PYXNvKwUHeMoQu0A9hR3bQDjU8WtI/BU9dYpOsTYKoAuaVIvFYAnS84lfr+rsLYWX5dUBELnt+JS2VF0GDNEfDc2rTV8+k4mKhkgvp+vJKLoKEXVLH6/Jykd
ILwTs60A6SaxeF7iyyexWenxwxGxu68A41PY6gowPkV4WSB4igCUwUgUw4/ENLc9hcc4GgcxPl0dwd+Vs+8A9FNFK0K/q7gvAP6uKnlF4GlSC4AuqWlGGJ+j
DFYB5p8mUhB0yVQKRL+rGoK/m5oh6KcZ1wVhfJpZBdBPZykdQJdcuHeAfnfB8PemXbsC8GyTItTBVl55DXrN+y3WtKY9As9RTQ5/1tq4f8h+/4ziI2lFKNsQ
czkeKT5Ylx1A2CS2ugeYhhB7PkDwzBnCnsIdg6c4hL+L9B2EPbUeEHRJTDH63QqGPT1j8My1IsyXpEAc5iLlBqHzKhnDnoqhS6p5D2FPM4x+N4x5nbpi2NMd
w57ZIHja1CHyI2MMnTcpGP2uGLpkhqFLZgYRN80w9NMcY55sbivCvM7cO0J+ZFkhxqdP/gyh350dwo9cMOKRK0Y8csXQeTcMnXfDmM+7OUQ92R1E590XiPHp
eUWo03o2iOObnn2BGJ9FIcZnnjDqyTlh5O+ZM0TekcUg6ktZMeYhWW0HwdMUg6djxKPsGPEoZ8bQz+wIl+3SuMceQt2mTAWi/llSgciPimLkR6UUgLuHENUJ
ow5Wp7pC8OQMkXe0CeM8gXV1j3/3EKJpD3EaC6XdMwT5JF4w7Ckdg6c2DJ5WQXi2I4J8kvszhGkyeT5i8MS4XIa8HjB4tj0Ez2wYfpQdw49yxvCjXDD8KFcQ
P2oQ5RDKfYHwo4JxOReVjDFfKgUiLaZSMfyoNIjDcVQ6xGmAVA3D36tjxM2adxj9XnYY9qwY/l4biL/3hsFz6RDxqDFGHtcwLouk5hh5R8O4fJNagTg9hFrF
0M/WMPSzdYjL9Kl1DJ3vDHEZCnXB0PmuGPl7N4x41H2F8PeOcTot9YIRj3rFiEe9YcSj3ivE+Fwgvm1i2LNDnB/Sl4LR7zNGvrkwxjxkcYz8aCkYOr/UhtHv
S4bguU4Y/r6uDuFHuwkjj9tPGPp5xEjjaIfhRrTHON2GDgcMnkeIu3sTvXxJEPiWQDATff/mzT9eLEvOnnM2HV/loioyvnqb1Zh55hNEOJ3AnOb0GMwpze9B
mjmNtj4X2wd8ZjMXktM0TPDN69d/OeSsOvZYth0dOzLYbnuUpvt9vTSQ5umpdq8Nv/eTp+n03nR6TPM0XhmvXf/dni4vnVY8b7X9Pm8wfTROmz7ENgYmoq9f
vXq+P9lA5JEN5lsb3Ozskza4/HHahRtjXNc+7eNlxz+C9r297pv4SBs88dqwwf8Be/vZFVitAAA=
'@ -replace '\s',''

# Add-Type cannot replace a type in a running session: after another version of this file was
# loaded, the C# below would fail with "type already exists" and the old engine would stay.
$script:SftaVersion = '2.0.1'
$loadedEngine = 'UserChoiceLatest' -as [type]
if ($loadedEngine) {
  $versionField = $loadedEngine.GetField('EngineVersion')
  $loadedVersion = if ($versionField) { $versionField.GetValue($null) } else { $null }
  if ($loadedVersion -ne $script:SftaVersion) {
    $loadedName = if ($loadedVersion) { "SFTA.ps1 $loadedVersion is" } else { 'An older SFTA.ps1 is' }
    throw "$loadedName already loaded in this PowerShell session, and its C# part cannot be replaced while the session runs. Open a new PowerShell window and load SFTA.ps1 $($script:SftaVersion) there."
  }
}

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using System.Text;

// Token encoder, lookup tables and mixing routines adapted from cssxn/UserChoiceLatestHash
// (MIT, Copyright (c) 2026 cssxn; see the license notice at the top of this file).
public static class UserChoiceLatest
{
    // Must equal $script:SftaVersion; tells a later load which version owns these types.
    public static readonly string EngineVersion = "2.0.1";

    // -- lookup tables (filled by LoadTables) --
    static byte[] ByteClass, TokenRegion, Fallback, BitWidth, ShortRewind, LongRewind;
    static bool _loaded;

    // Post-mix magic seeds (recovered from the DLL's two mixing routines).
    static readonly uint[] SeedA = { 0x69FB0000U, 0x79F8A395U, 0x3CE8EC25U, 0x10FA9605U };
    static readonly uint[] SeedB = { 0x12CEB96DU, 0x2B890000U, 0x5B9F0000U, 0x2C7C6901U };

    const ulong SENTINEL = 0xFBFFFFFFFFFFFFFFUL;
    const ulong TOK_A = 0xFE00000000000000UL;
    const ulong TOK_B = 0xFE00800000000000UL;
    const ulong TOK_C = 0xFF00000000000000UL;
    const ulong TOK_D = 0xFE007DFFFFFFFFFFUL;
    const ulong TOK_E = 0xFE00801F7FFFFFFFUL;
    const uint  SEEN_MOD = 0x2803U;

    public static void LoadTables(string gzipBase64)
    {
        if (_loaded) return;
        byte[] raw;
        using (var ms = new MemoryStream(Convert.FromBase64String(gzipBase64)))
        using (var gz = new GZipStream(ms, CompressionMode.Decompress))
        using (var outp = new MemoryStream())
        {
            gz.CopyTo(outp);
            raw = outp.ToArray();
        }
        int pos = 0;
        ByteClass   = ReadChunk(raw, ref pos);
        TokenRegion = ReadChunk(raw, ref pos);
        Fallback    = ReadChunk(raw, ref pos);
        BitWidth    = ReadChunk(raw, ref pos);
        ShortRewind = ReadChunk(raw, ref pos);
        LongRewind  = ReadChunk(raw, ref pos);
        _loaded = true;
    }

    static byte[] ReadChunk(byte[] src, ref int pos)
    {
        int len = BitConverter.ToInt32(src, pos); pos += 4;
        byte[] b = new byte[len];
        Buffer.BlockCopy(src, pos, b, 0, len); pos += len;
        return b;
    }

    // --------------------------- public entry -----------------------------
    // Lowercase ASCII only - matches the CRT _wcslwr (C locale) that the OS applies
    // to the canonical input. Non-ASCII bytes are left untouched, as the OS does.
    static string AsciiLower(string s)
    {
        char[] c = s.ToCharArray();
        for (int i = 0; i < c.Length; i++) if (c[i] >= 'A' && c[i] <= 'Z') c[i] = (char)(c[i] + 32);
        return new string(c);
    }

    public static bool NativeAvailable { get { return NativeUcl.Available; } }
    public static string NativeLocation { get { return NativeUcl.LocatedAt; } }

    // The hash = tokenize(canonical) -> MD5 + two mixes. The tokenizer is delegated to
    // Windows' own OpenWithHost.dll when it was found and agreed with the managed tokenizer;
    // the managed tokenizer is the self-contained fallback (exact for all real associations).
    public static string ComputeHash(string canonicalInput)
    {
        string lowered = AsciiLower(canonicalInput);
        ushort[] packed = null;
        if (NativeUcl.Available) { try { packed = NativeUcl.Encode(lowered); } catch { packed = null; } }
        if (packed == null) packed = EncodeManaged(lowered);
        return HashFromPacked(packed);
    }

    // Same as ComputeHash but never uses the native encoder, so the fallback can be checked
    // against real hashes on a machine that has the DLL.
    public static string ComputeHashManaged(string canonicalInput)
    {
        return HashFromPacked(EncodeManaged(AsciiLower(canonicalInput)));
    }

    public static ushort[] EncodeManaged(string lowered)
    {
        char[] inp = lowered.ToCharArray();
        var s = new State();
        uint firstConsumed;
        FirstPass(s, inp, inp.Length, out firstConsumed);
        SecondPass(s, inp, (int)firstConsumed, inp.Length);
        s.Entries[0] = (ulong)s.TotalUnits | (s.Count << 32);

        var packed = new List<ushort>();
        foreach (ulong v in s.Entries)
            for (int sh = 0; sh < 64; sh += 16)
            {
                ushort part = (ushort)(v >> sh);
                if (part != 0) packed.Add(part);
            }
        packed.Add(0);
        return packed.ToArray();
    }

    // The legacy UserChoice hash is the same MD5 + two mixes, applied to the UTF-16 string plus
    // its terminator instead of the token stream. Inputs too short to mix give "", as in the
    // original PS-SFTA PowerShell implementation this replaces.
    public static string LegacyHash(string baseInfo)
    {
        ushort[] units = new ushort[baseInfo.Length + 1];
        for (int i = 0; i < baseInfo.Length; i++) units[i] = baseInfo[i];
        if (MixCount(units.Length * 2) < 2) return "";
        return HashFromPacked(units);
    }

    static int MixCount(int byteLen)
    {
        int dwordLen = byteLen >> 2;
        return ((byteLen & 4) == 0) ? dwordLen : dwordLen - 1;
    }

    static string HashFromPacked(ushort[] packed)
    {
        byte[] bytes = new byte[packed.Length * 2];
        for (int i = 0; i < packed.Length; i++)
        {
            bytes[2 * i]     = (byte)packed[i];
            bytes[2 * i + 1] = (byte)(packed[i] >> 8);
        }

        uint[] md5 = new uint[4];
        using (var md = MD5.Create())
        {
            byte[] h = md.ComputeHash(bytes);
            for (int i = 0; i < 4; i++) md5[i] = BitConverter.ToUInt32(h, i * 4);
        }

        int mixCount = MixCount(bytes.Length);
        uint[] dw = new uint[(packed.Length + 1) / 2];
        for (int i = 0; i < dw.Length; i++)
        {
            uint lo = packed[i * 2];
            uint hi = (i * 2 + 1 < packed.Length) ? packed[i * 2 + 1] : 0U;
            dw[i] = lo | (hi << 16);
        }

        uint[] pa = new uint[2], pb = new uint[2];
        if (mixCount >= 2)
        {
            MixA(dw, mixCount, md5, SeedA, pa);
            MixB(dw, mixCount, md5, SeedB, pb);
        }

        byte[] final8 = new byte[8];
        Array.Copy(BitConverter.GetBytes(pa[0] ^ pb[0]), 0, final8, 0, 4);
        Array.Copy(BitConverter.GetBytes(pa[1] ^ pb[1]), 0, final8, 4, 4);
        return Convert.ToBase64String(final8);
    }

    // ----------------------------- internals -------------------------------
    class State
    {
        public List<ulong> Entries = new List<ulong>();
        public ulong[] Seen = new ulong[161];
        public ulong Last = SENTINEL;
        public uint Repeat;
        public uint TotalUnits;
        public State() { Entries.Add(0UL); }
        public ulong Count { get { return (ulong)Entries.Count; } }
    }

    static uint Idx(ulong v) { return (uint)(v % SEEN_MOD); }
    static bool TestSeen(State s, ulong v) { uint i = Idx(v); return (s.Seen[i >> 6] & (1UL << (int)(i & 63))) != 0; }
    static void MarkSeen(State s, ulong v) { uint i = Idx(v); s.Seen[i >> 6] |= (1UL << (int)(i & 63)); }
    static void Push(State s, ulong v) { s.Entries.Add(v); }

    static bool FindPrevious(State s, ulong value, out ulong distance)
    {
        distance = 0;
        if (!TestSeen(s, value) || s.Count == 0) return false;
        ulong start = s.Count > 0x400 ? s.Count - 0x400 : 0;
        for (ulong idx = s.Count - 1; ; --idx)
        {
            if (s.Entries[(int)idx] == value) { distance = s.Count - idx; return true; }
            if (idx == start) break;
        }
        return false;
    }

    static byte ClassFromWord(ushort ch)
    {
        byte hi = (byte)(ch >> 8);
        if (hi != 0) return (byte)(ByteClass[hi] + 8);
        return ByteClass[ch & 0xFF];
    }

    static bool BuildToken(char[] inp, int cursor, int end, out ulong token, out byte kind, out byte len)
    {
        token = 0; kind = 0; len = 0;
        if (cursor >= end) return false;
        uint code = TokenRegion[ClassFromWord(inp[cursor]) + 256];
        uint tokenLen = TokenRegion[code];
        if (cursor + tokenLen > end) return false;
        uint tableBase = 168U * code;
        for (;;)
        {
            uint p = 0;
            if (tokenLen == 0) break;
            int probe = cursor;
            bool done = false;
            while ((ulong)inp[probe] < (1UL << BitWidth[tableBase + p]))
            {
                ++p; ++probe;
                if (p >= tokenLen) { done = true; break; }
            }
            if (done) break;
            ++code; tableBase = 168U * code; tokenLen = TokenRegion[code];
            if (tokenLen > (uint)(end - cursor)) break;
        }
        ulong t = (ulong)(byte)code << 56;
        int shift = 56;
        for (uint i = 0; i < (byte)tokenLen; ++i)
        {
            shift -= BitWidth[tableBase + i];
            t |= (ulong)inp[cursor + i] << shift;
        }
        token = t; kind = (byte)code; len = (byte)tokenLen;
        return true;
    }

    static bool BuildTokenSecond(char[] inp, int cursor, int end, out ulong token, out byte kind, out byte len)
    {
        token = 0; kind = 0; len = 0;
        if (cursor >= end) return false;
        uint remain = (uint)(end - cursor);
        uint code = TokenRegion[ClassFromWord(inp[cursor]) + 256];
        uint tokenLen = TokenRegion[code];
        if (tokenLen > remain) { code = Fallback[remain]; tokenLen = TokenRegion[code]; }
        uint tableBase = 168U * code;
    retry:
        uint p = 0;
        if (tokenLen != 0)
        {
            int probe = cursor;
            while (probe < end)
            {
                if ((ulong)inp[probe] >= (1UL << BitWidth[tableBase + p]))
                {
                    ++code; tableBase = 168U * code; tokenLen = TokenRegion[code];
                    if (tokenLen <= remain) goto retry;
                    break;
                }
                ++p; ++probe;
                if (p >= tokenLen) break;
            }
        }
        ulong t = (ulong)(byte)code << 56;
        int shift = 56;
        for (uint i = 0; i < (byte)tokenLen; ++i)
        {
            if (cursor + i >= end) break;
            shift -= BitWidth[tableBase + i];
            t |= (ulong)inp[cursor + i] << shift;
        }
        token = t; kind = (byte)code; len = (byte)tokenLen;
        return true;
    }

    // A run of repeated tokens just ended: emit it as a back-reference and rewind the cursor.
    // False when the repeated token is outside the window (the caller then emits the token).
    static bool TryFlushRepeat(State s, ref int cursor, ulong token, byte kind)
    {
        ulong distance;
        if (!FindPrevious(s, s.Last, out distance)) return false;
        uint rep1 = s.Repeat - 1;
        ulong entry;
        if (distance >= 0x800)
        {
            ulong packed = (token >> 17) | ((ulong)rep1 << 48) | TOK_A;
            MarkSeen(s, packed); Push(s, packed);
            entry = s.Last;
        }
        else
        {
            entry = (token >> 27) | ((ulong)((((uint)(ushort)distance - 1U) & 0x3FFU) | (rep1 << 11)) << 37) | TOK_B;
        }
        MarkSeen(s, entry); Push(s, entry);
        s.Last = SENTINEL; s.Repeat = 0;
        cursor -= (distance >= 0x800) ? LongRewind[kind] : ShortRewind[kind];
        return true;
    }

    static void EmitFirst(State s, ref int cursor, int end, char[] inp, ulong token, byte kind)
    {
        if (token == s.Last && s.Repeat < 0xFF) { ++s.Repeat; return; }
        if (s.Repeat != 0)
        {
            if (TryFlushRepeat(s, ref cursor, token, kind)) return;
        }
        else
        {
            ulong distance;
            if (FindPrevious(s, token, out distance) && cursor < end)
            {
                byte nextKind = TokenRegion[ClassFromWord(inp[cursor]) + 256];
                ulong entry = distance >= 0x800 ? token : ((token >> 18) | ((distance - 1UL) << 46) | TOK_C);
                MarkSeen(s, entry); Push(s, entry);
                if (distance < 0x800) cursor -= ShortRewind[nextKind + 256];
                s.Last = token;
                return;
            }
        }
        MarkSeen(s, token); Push(s, token); s.Last = token;
    }

    static void EmitSecond(State s, ref int cursor, ulong token, byte kind)
    {
        if (token == s.Last && s.Repeat < 0xFF) { ++s.Repeat; return; }
        if (s.Repeat != 0 && TryFlushRepeat(s, ref cursor, token, kind)) return;
        MarkSeen(s, token); Push(s, token); s.Last = token;
    }

    static void FlushSecond(State s)
    {
        if (s.Repeat == 0) return;
        ulong entry;
        if (s.Last == SENTINEL)
        {
            entry = ((ulong)s.Repeat << 48) | TOK_D;
        }
        else
        {
            ulong distance;
            if (FindPrevious(s, s.Last, out distance) && distance != 0)
            {
                entry = ((ulong)((((uint)(ushort)distance - 1U) & 0x3FFU) | ((s.Repeat - 1U) << 11)) << 37) | TOK_E;
            }
            else
            {
                ulong rt = ((ulong)s.Repeat << 48) | TOK_D;
                MarkSeen(s, rt); Push(s, rt);
                entry = s.Last;
            }
        }
        MarkSeen(s, entry); Push(s, entry);
        s.Repeat = 0; s.Last = SENTINEL;
    }

    static void FirstPass(State s, char[] inp, int n, out uint consumed)
    {
        int end = n, cursor = 0;
        ulong result = (2249744775UL * (uint)n) >> 32;
        consumed = 0;
        ulong quota = (result + (((ulong)n - result) >> 1)) >> 7;
        while ((uint)quota > 1)
        {
            uint limit = (uint)quota - 1;
            uint processed = 0;
            while (processed < limit)
            {
                ulong token; byte kind, len;
                if (!BuildToken(inp, cursor, end, out token, out kind, out len)) break;
                consumed += len; cursor += len;
                EmitFirst(s, ref cursor, end, inp, token, kind);
                ++processed;
            }
            result = (2249744775UL * (uint)(end - cursor)) >> 32;
            quota = ((uint)result + (((uint)(end - cursor) - (uint)result) >> 1)) >> 7;
        }
    }

    static void SecondPass(State s, char[] inp, int start, int end)
    {
        int cursor = start;
        while (cursor < end)
        {
            ulong token; byte kind, len;
            if (!BuildTokenSecond(inp, cursor, end, out token, out kind, out len)) break;
            s.TotalUnits += len; cursor += len;
            EmitSecond(s, ref cursor, token, kind);
        }
        FlushSecond(s);
    }

    static void MixA(uint[] p, int count, uint[] md5, uint[] seed, uint[] outPair)
    {
        unchecked
        {
            uint md0 = md5[0] | 1U, md1 = (md5[1] | 1U) + 0x13DB0000U;
            uint sum = 0, acc = 0; int i = 0;
            uint loops = (((uint)count - 2U) >> 1) + 1U;
            uint mul = md0 + seed[0];
            while (loops-- != 0)
            {
                acc += p[i];
                uint x = acc, hi = x >> 16; i += 2;
                uint t = x * mul - hi * seed[3];
                uint ss = t * seed[1] + (t >> 16) * 0x689B6B9FU;
                uint part1 = ss * 0xEA970001U - (ss >> 16) * 0x3C101569U;
                uint w = p[i - 1] + part1;
                uint t2 = w * md1 - (w >> 16) * seed[2];
                uint temp = t2 * 0x59C3AF2DU - (t2 >> 16) * 0x2232E0F1U;
                acc = (temp >> 16) * 0x35BD1EC9U + temp * 0x1EC90001U;
                sum += part1 + acc;
            }
            outPair[0] = acc; outPair[1] = sum;
        }
    }

    static void MixB(uint[] p, int count, uint[] md5, uint[] seed, uint[] outPair)
    {
        unchecked
        {
            uint md1 = md5[1] | 1U, md0 = md5[0] | 1U;
            uint sum = 0, acc = 0; int idx = 0;
            uint loops = (((uint)count - 2U) >> 1) + 1U;
            while (loops-- != 0)
            {
                uint x = p[idx] + acc; idx += 2;
                uint t = x * md0;
                uint a = t * 0xB1110000U - (t >> 16) * 0x30674EEFU;
                uint b = a * seed[2];
                uint c = b - (a >> 16) * 0x78F7A461U;
                c = (c >> 16) * seed[0] - (a >> 16) * 0x164D0000U - b * 0x46930000U;
                uint d = (c >> 16) * 0x257E1D83U + c * 0x1D830000U;
                uint e = p[idx - 1] + d;
                uint f = e * md1;
                uint g = f * 0x16F50000U - (f >> 16) * 0x5D8BE90BU;
                uint h = (g >> 16) * seed[3];
                uint ii = g * 0x69010000U + h;
                uint j = ii * seed[1];
                uint k = ((0U - h) - g * 0x69010000U) >> 16;
                uint m = k * 0x7C932B89U - j;
                uint nn = k * 0xF2310000U - (m >> 16) * 0x405B6097U;
                nn += j * 0x60970000U;
                acc = nn; sum += d + acc;
            }
            outPair[0] = acc; outPair[1] = sum;
        }
    }
}

// Locates Windows' own UserChoiceLatest token-encoder inside
// Windows.Internal.OpenWithHost.dll by signature (not a fixed offset), accepts it only if it
// encodes a set of probe strings exactly like the managed encoder, and exposes Encode().
// When it is missing, moved or disagrees, the managed encoder in UserChoiceLatest is used.
public static class NativeUcl
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr LoadLibraryW(string name);

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    delegate void EncodeFn(IntPtr text, int length, IntPtr output);

    static EncodeFn _encode;
    public static bool Available { get; private set; }
    public static string LocatedAt = "";

    // Function prologue + characteristic 0x2808-byte stack-frame lea.
    static readonly byte[] Sig = {
        0x48,0x89,0x5C,0x24,0x08,0x55,0x56,0x57,0x41,0x54,0x41,0x55,0x41,0x56,0x41,0x57,
        0x48,0x8D,0xAC,0x24,0x90,0xD8,0xFF,0xFF
    };

    public static void Init()
    {
        if (Available) return;
        try
        {
            IntPtr mod = LoadLibraryW(@"C:\Windows\System32\Windows.Internal.OpenWithHost.dll");
            if (mod == IntPtr.Zero) return;
            long baseAddr = mod.ToInt64();
            int peOff = Marshal.ReadInt32(mod, 0x3C);
            short nSec = Marshal.ReadInt16(mod, peOff + 6);
            short optSize = Marshal.ReadInt16(mod, peOff + 20);
            long secTab = peOff + 24 + optSize;
            long scanStart = 0, scanLen = 0;
            for (int i = 0; i < nSec; i++)
            {
                long o = secTab + 40 * i;
                byte[] nameB = new byte[8];
                for (int j = 0; j < 8; j++) nameB[j] = Marshal.ReadByte(mod, (int)(o + j));
                string name = Encoding.ASCII.GetString(nameB).TrimEnd('\0');
                if (name == ".text")
                {
                    uint vsize = (uint)Marshal.ReadInt32(mod, (int)(o + 8));
                    uint va = (uint)Marshal.ReadInt32(mod, (int)(o + 12));
                    scanStart = va; scanLen = vsize; break;
                }
            }
            if (scanLen == 0) return;
            for (long off = scanStart; off < scanStart + scanLen - Sig.Length; off++)
            {
                bool hit = true;
                for (int k = 0; k < Sig.Length; k++)
                    if (Marshal.ReadByte(mod, (int)(off + k)) != Sig[k]) { hit = false; break; }
                if (!hit) continue;
                var cand = (EncodeFn)Marshal.GetDelegateForFunctionPointer(new IntPtr(baseAddr + off), typeof(EncodeFn));
                if (SelfTest(cand)) { _encode = cand; Available = true; LocatedAt = "0x" + off.ToString("X"); return; }
            }
        }
        catch { Available = false; }
    }

    static bool SelfTest(EncodeFn fn)
    {
        try
        {
            // Neutral probes: confirm the located function encodes exactly like our managed
            // encoder across varied input. A wrong (coincidental) signature match diverges here.
            string[] probes = {
                "abc",
                "hello world test 123",
                ".pdfs-1-5-21-1-2-3-500chromehtml01db520e5e240400d9087240-1a6f-40cc-9ea2-3349035204d6",
                "a0b1c2d3-e4f5-6789-abcd-ef0123456789.htm01db000011112222applications\\notepad.exes-1-5-21-111111111-2222222222-3333333333-1001"
            };
            foreach (var p in probes)
            {
                ushort[] nat = EncodeWith(fn, p);
                ushort[] man = UserChoiceLatest.EncodeManaged(p);
                if (nat == null || man == null || nat.Length != man.Length) return false;
                for (int i = 0; i < nat.Length; i++) if (nat[i] != man[i]) return false;
            }
            return true;
        }
        catch { return false; }
    }

    static ushort[] EncodeWith(EncodeFn fn, string lowered)
    {
        IntPtr text = Marshal.StringToHGlobalUni(lowered);
        IntPtr outp = Marshal.AllocHGlobal(24);
        try
        {
            Marshal.WriteInt64(outp, 0, 0);
            Marshal.WriteInt64(outp, 8, -1);
            Marshal.WriteInt64(outp, 16, -1);
            fn(text, lowered.Length, outp);
            IntPtr buf = Marshal.ReadIntPtr(outp, 0);
            if (buf == IntPtr.Zero) return null;
            long declared = Marshal.ReadInt64(outp, 8);
            var words = new List<ushort>();
            for (int i = 0; i < (1 << 20); i++)
            {
                ushort w = (ushort)Marshal.ReadInt16(buf, i * 2);
                words.Add(w);
                if (declared >= 0 ? i >= declared : w == 0) break;
            }
            Marshal.FreeCoTaskMem(buf);
            return words.ToArray();
        }
        finally { Marshal.FreeHGlobal(text); Marshal.FreeHGlobal(outp); }
    }

    public static ushort[] Encode(string lowered)
    {
        if (!Available) return null;
        return EncodeWith(_encode, lowered);
    }
}
'@

[UserChoiceLatest]::LoadTables($script:UclTablesBlob)
[NativeUcl]::Init()

# ---------------------------------------------------------------------------
#  Native helpers: registry last-write time, legacy UserChoice key deleter, shell refresh.
# ---------------------------------------------------------------------------
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class SftaNative
{
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode)]
    static extern int RegOpenKeyExW(UIntPtr hKey, string sub, int opt, int sam, out UIntPtr res);
    [DllImport("advapi32.dll")]
    static extern int RegQueryInfoKeyW(UIntPtr h, IntPtr c, IntPtr cc, IntPtr r, IntPtr sk,
        IntPtr msk, IntPtr mc, IntPtr v, IntPtr mvn, IntPtr mvl, IntPtr sd, out long ft);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode)]
    static extern uint RegDeleteKeyW(UIntPtr hKey, string sub);
    [DllImport("advapi32.dll")]
    static extern int RegCloseKey(UIntPtr h);
    [DllImport("shell32.dll")]
    static extern void SHChangeNotify(int eventId, uint flags, IntPtr item1, IntPtr item2);

    const ulong HKCU = 0x80000001UL;
    const int SHCNE_ASSOCCHANGED = 0x08000000;

    // Returns the key's FILETIME (100ns ticks), or -1 if the key is missing.
    public static long GetLastWriteTime(string subKeyHkcu)
    {
        UIntPtr h;
        if (RegOpenKeyExW((UIntPtr)HKCU, subKeyHkcu, 0, 0x20019, out h) != 0) return -1;
        long ft;
        int rc = RegQueryInfoKeyW(h, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero,
            IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, out ft);
        RegCloseKey(h);
        return rc == 0 ? ft : -1;
    }

    // Delete a UserChoice key the way SetUserFTA does: a raw advapi32 call that
    // UCPD's registry filter does not block for the plain "UserChoice" leaf.
    public static void DeleteKey(string subKeyHkcu)
    {
        RegDeleteKeyW((UIntPtr)HKCU, subKeyHkcu);
    }

    // Tells Explorer to reload associations, so a change applies without restarting it.
    public static void NotifyAssocChanged()
    {
        SHChangeNotify(SHCNE_ASSOCCHANGED, 0, IntPtr.Zero, IntPtr.Zero);
    }
}
'@

# ---------------------------------------------------------------------------
#  Canonical-string construction (per machine class 0/1/2).
# ---------------------------------------------------------------------------

# class -> field order. Fields are indexed:
#   0 assoc   1 sid   2 progid   3 timestampHex   4 salt   5 machineId
# Orders and salt->class mapping were read straight from the v1 table in
# Windows.Internal.OpenWithHost.dll (PTR @ 0x180077be0) and match the live registry.
$script:UclFieldOrder = @{
  0 = @(4, 0, 3, 5, 2, 1)
  1 = @(4, 3, 0, 1, 5, 2)
  2 = @(1, 3, 4, 0, 5, 2)
}
$script:UclSaltGuid = @{
  0 = '{3822B7CA-C2F4-4889-B8CC-4CE39A8FB81C}'
  1 = '{D185E0A1-E265-4724-AA21-3A17B038D72E}'
  2 = '{97B6BCF4-C367-4577-95BE-73BD3053A5E0}'
}
# A SECOND salt/order set ("v2") also exists in OpenWithHost.dll (PTR @ 0x180077c60),
# selected when the OS reports hash version 2. No shipping Windows build activates it yet,
# so it is recorded here for future maintainers but NOT used for writing. Do not enable it
# blindly: the trigger (feature flags vs. the HashVersion value) is not confirmed, and it
# cannot be verified against a live machine today.
#   class0 order [2,4,0,1,3,5] salt {465177C0-F2AE-47CB-A4E5-665F5010B33B}
#   class1 order [0,2,4,5,3,1] salt {C274C9B4-CEB0-439E-A63B-1462A9CA8DB3}
#   class2 order [3,4,5,0,2,1] salt {2F201AA9-99A8-4449-8F23-1AA5DCC8356C}
# Safety net: Get-UclWritePlan refuses when HashVersion is above 1, and Set-FTA refuses when
# Test-UclSelfCheck cannot reproduce any existing association, so a rotation to a scheme this
# script does not implement never leads to a wrong hash being written silently.

function Get-UclMachineId {
  $raw = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\SQMClient' -Name MachineId -ErrorAction SilentlyContinue).MachineId
  if ([string]::IsNullOrEmpty($raw)) { return $null }
  return $raw.Trim('{', '}')
}

function Get-UclUserSid {
  ([System.Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
}

function Get-UclMachineClass {
  param([Parameter(Mandatory)][string]$MachineId)
  [int][char]$MachineId[-1] % 3
}

# The only UserChoiceLatest scheme this script implements (salts + field order below).
$script:UclSupportedSchemeVersion = 1

function Get-UclSchemeVersion {
  # The per-user HashVersion flag: 0/absent = scheme off, 1 = v1. Windows already ships a second
  # salt/order set ("v2", see below); a higher value means it has rotated to a scheme this script
  # does not implement.
  $key = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\SystemProtectedUserData\$(Get-UclUserSid)\AnyoneRead\AppDefaults"
  $version = (Get-ItemProperty $key -Name HashVersion -ErrorAction SilentlyContinue).HashVersion
  if ($null -eq $version) { return 0 }
  return [int]$version
}

function Test-UclSchemeActive {
  # Once the HashVersion flag is set, Explorer reads ONLY UserChoiceLatest.
  return ((Get-UclSchemeVersion) -ge 1)
}

$script:UclSaltCache = @{}
function Get-UclSaltForClass {
  # The salt is an 83-character Microsoft string ("Copyright (C) ..." ending in a GUID) embedded
  # in windows.storage.dll / shell32.dll / OpenWithHost.dll. We never ship a copy of it; we read
  # it from the running machine's own DLL, located by the GUID that ends it. If Microsoft retires
  # these GUIDs, the lookup fails and Get-UclWritePlan refuses to write (or falls back to the
  # legacy key where the scheme is off), so a wrong hash is never written.
  param([int]$MachineClass)
  if ($script:UclSaltCache.ContainsKey($MachineClass)) { return $script:UclSaltCache[$MachineClass] }
  $guid = $script:UclSaltGuid[$MachineClass]
  $result = $null
  foreach ($dll in @("$env:WINDIR\System32\windows.storage.dll", "$env:WINDIR\System32\shell32.dll")) {
    try {
      $bytes = [System.IO.File]::ReadAllBytes($dll)
      $text = [System.Text.Encoding]::Unicode.GetString($bytes)
      $i = $text.IndexOf($guid, [System.StringComparison]::OrdinalIgnoreCase)
      if ($i -ge 0) {
        $start = $text.LastIndexOf([char]0, $i) + 1
        $end = $text.IndexOf([char]0, $i)
        if ($end -gt $start) { $result = $text.Substring($start, $end - $start); break }
      }
    }
    catch { }
  }
  if ($null -eq $result) {
    throw "Could not read the association salt for machine class $MachineClass from the shell DLLs. UserChoiceLatest cannot be computed on this machine."
  }
  $script:UclSaltCache[$MachineClass] = $result
  return $result
}

function Get-UclTimestampHex {
  # Round the ProgId subkey's FILETIME to whole milliseconds (SYSTEMTIME round-trip),
  # then format high/low dwords. Matches userchoicecore.cpp exactly.
  param([long]$FileTimeTicks)
  $ft = $FileTimeTicks - ($FileTimeTicks % 10000L)
  '{0:x8}{1:x8}' -f ($ft -shr 32), ($ft -band 0xFFFFFFFFL)
}

function Get-UclCanonicalInput {
  param(
    [Parameter(Mandatory)][string]$Assoc,
    [Parameter(Mandatory)][string]$ProgId,
    [Parameter(Mandatory)][string]$Sid,
    [Parameter(Mandatory)][string]$MachineId,
    [Parameter(Mandatory)][string]$TimestampHex
  )
  $class = Get-UclMachineClass -MachineId $MachineId
  $salt = Get-UclSaltForClass -MachineClass $class
  $fields = @($Assoc, $Sid, $ProgId, $TimestampHex, $salt, $MachineId)
  $order = $script:UclFieldOrder[$class]
  -join ($order | ForEach-Object { $fields[$_] })
}

function Get-UclHash {
  # Compute a UserChoiceLatest hash for a known FILETIME (used by the canary check).
  param(
    [Parameter(Mandatory)][string]$Assoc,
    [Parameter(Mandatory)][string]$ProgId,
    [Parameter(Mandatory)][long]$FileTimeTicks
  )
  $sid = Get-UclUserSid
  $mid = Get-UclMachineId
  if (-not $mid) { throw "SQMClient MachineId not found - UserChoiceLatest cannot be computed." }
  $ts = Get-UclTimestampHex -FileTimeTicks $FileTimeTicks
  $canon = Get-UclCanonicalInput -Assoc $Assoc -ProgId $ProgId -Sid $sid -MachineId $mid -TimestampHex $ts
  # ComputeHash lowercases ASCII-only internally (matching the OS); pass the canonical raw.
  [UserChoiceLatest]::ComputeHash($canon)
}

function Get-UclWritePlan {
  # Decides, before anything is written, whether Set-FTA can produce UserChoiceLatest:
  #   'write'  - MachineId and salt are available; compute and write it
  #   'skip'   - they are not (e.g. Windows 10, or a build without the feature), but the
  #              scheme is inactive, so the legacy UserChoice key is authoritative
  #   'refuse' - they are not, yet Explorer reads only UserChoiceLatest here: any write
  #              would be silently ignored; or Windows uses a newer scheme than v1
  $scheme = Get-UclSchemeVersion
  if ($scheme -gt $script:UclSupportedSchemeVersion) {
    return [pscustomobject]@{
      Action = 'refuse'
      Reason = "Windows uses UserChoiceLatest hash scheme v$scheme and this script implements v$($script:UclSupportedSchemeVersion) only; it needs an update"
    }
  }
  $reason = $null
  $mid = Get-UclMachineId
  if (-not $mid) { $reason = 'SQMClient MachineId not found' }
  else {
    try { $null = Get-UclSaltForClass -MachineClass (Get-UclMachineClass -MachineId $mid) }
    catch { $reason = $_.Exception.Message }
  }
  $action = if (-not $reason) { 'write' } elseif (Test-UclSchemeActive) { 'refuse' } else { 'skip' }
  [pscustomobject]@{ Action = $action; Reason = $reason }
}

# ---------------------------------------------------------------------------
#  Legacy UserChoice hash (the original PS-SFTA algorithm).
# ---------------------------------------------------------------------------
function Get-LegacyUserChoiceHash {
  # Computed by the C# engine: the legacy hash is the same MD5 + two mixes that UserChoiceLatest
  # applies to its token stream. The tests pin it to the original PowerShell code's output and to
  # the hashes Windows itself wrote.
  [CmdletBinding()]
  param([Parameter(Mandatory)][string]$BaseInfo)
  [UserChoiceLatest]::LegacyHash($BaseInfo)
}

# ---------------------------------------------------------------------------
#  UCPD (UserChoice Protection Driver) detection.
# ---------------------------------------------------------------------------
$script:UcpdProtected = @('http', 'https', '.pdf', '.htm', '.html', '.svg', '.xml',
  '.mht', '.mhtml', '.xhtml', '.xht', '.webp', '.shtml', 'ftp')

function Get-UcpdState {
  $svc = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\UCPD' -Name Start -ErrorAction SilentlyContinue
  $running = $false
  try { $running = (Get-CimInstance Win32_SystemDriver -Filter "Name='UCPD'" -ErrorAction SilentlyContinue).State -eq 'Running' } catch { }
  [pscustomobject]@{
    StartValue = if ($svc) { $svc.Start } else { $null }
    Running    = $running
    # Active = the driver is currently loaded and will block protected writes. It equals Running
    # today; both stay, as callers may read either.
    Active     = $running
  }
}

function Disable-Ucpd {
  <#
  .SYNOPSIS  Disable the UserChoice Protection Driver (needs admin, reboot to take effect).
  .DESCRIPTION
      Only required when you need to change a PROTECTED association (http, https, .pdf, ...)
      while UCPD is loaded. Ordinary extensions are writable even with UCPD active.
  #>
  [CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
  param([switch]$Force)

  $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if (-not $admin) { throw "Disable-Ucpd requires an elevated (Administrator) PowerShell." }

  if (-not ($Force -or $PSCmdlet.ShouldProcess('UCPD driver + velocity task', 'Disable'))) { return }

  # The "UCPD velocity" task re-enables the driver (still present on 26300, but listed only to
  # an elevated session). Search everywhere instead of assuming a fixed path.
  $tasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match 'UCPD' })
  if ($tasks.Count -eq 0) {
    Write-Output "No UCPD re-enable task found on this build (nothing to disable)."
  }
  foreach ($t in $tasks) {
    $null = $t | Disable-ScheduledTask -ErrorAction Stop
    Write-Output "Disabled task: $($t.TaskPath)$($t.TaskName)"
  }

  Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\UCPD' -Name 'Start' -Value 4 -Force
  $start = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\UCPD' -Name Start).Start
  Write-Output "UCPD service Start = $start (4 = disabled)."
  Write-Warning "REBOOT required before protected associations (http/https/.pdf/...) can be changed."
}

# ---------------------------------------------------------------------------
#  Self-verification canary: prove the embedded algorithm still matches what
#  Windows wrote, using a real existing UserChoiceLatest entry on this machine.
# ---------------------------------------------------------------------------
function Test-UclSelfCheck {
  # Recompute an existing live UserChoiceLatest hash and compare. Returns:
  #   'ok'        - at least one existing entry reproduced exactly (algorithm valid here)
  #   'mismatch'  - entries exist but NONE reproduced (algorithm does not match this OS)
  #   'noentries' - nothing to verify against yet
  [CmdletBinding()]
  [OutputType([string])]
  param([switch]$Quiet)

  $tested = 0
  foreach ($root in $script:FileExtsRoot, $script:UrlAssocRoot) {
    $base = "HKCU:\$root"
    if (-not (Test-Path $base)) { continue }
    foreach ($k in Get-ChildItem $base -ErrorAction SilentlyContinue) {
      $assoc = $k.PSChildName
      $latest = "$base\$assoc\UserChoiceLatest"
      if (-not (Test-Path $latest)) { continue }
      $storedHash = (Get-ItemProperty $latest -Name Hash -ErrorAction SilentlyContinue).Hash
      $progId = (Get-ItemProperty "$latest\ProgId" -Name ProgId -ErrorAction SilentlyContinue).ProgId
      if ([string]::IsNullOrEmpty($storedHash) -or [string]::IsNullOrEmpty($progId)) { continue }
      $ticks = [SftaNative]::GetLastWriteTime("$root\$assoc\UserChoiceLatest\ProgId")
      if ($ticks -lt 0) { continue }
      $tested++
      $calc = Get-UclHash -Assoc $assoc -ProgId $progId -FileTimeTicks $ticks
      if ($calc -ceq $storedHash) {
        if (-not $Quiet) { Write-Verbose "Self-check OK on '$assoc' ($progId): $calc" }
        return 'ok'
      }
    }
  }
  if ($tested -eq 0) {
    if (-not $Quiet) { Write-Warning "No existing UserChoiceLatest entry to verify the algorithm against yet." }
    return 'noentries'
  }
  if (-not $Quiet) { Write-Warning "UserChoiceLatest self-check FAILED: none of $tested existing entries reproduced. Windows may have changed the algorithm." }
  return 'mismatch'
}

# ---------------------------------------------------------------------------
#  ApplicationAssociationToasts (keeps Settings from re-prompting).
# ---------------------------------------------------------------------------
function Write-ApplicationAssociationToasts {
  param([string]$ProgId, [string]$Extension)
  try {
    [Microsoft.Win32.Registry]::SetValue(
      "HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion\ApplicationAssociationToasts",
      $ProgId + "_" + $Extension, 0x0)
  }
  catch { }
  $list = @()
  $list += Get-ChildItem -Path HKLM:\SOFTWARE\Classes\$Extension\OpenWithList\* -ErrorAction SilentlyContinue | ForEach-Object { "Applications\$($_.PSChildName)" }
  $list += @(foreach ($item in (Get-ItemProperty -Path HKLM:\SOFTWARE\Classes\$Extension\OpenWithProgids -ErrorAction SilentlyContinue).PSObject.Properties) {
      if ([string]::IsNullOrEmpty($item.Value) -and $item.Name -ne '(default)') { $item.Name }
    })
  $list += Get-ChildItem -Path HKLM:SOFTWARE\Clients\StartMenuInternet\*, HKCU:SOFTWARE\Clients\StartMenuInternet\* -ErrorAction SilentlyContinue | ForEach-Object {
    (Get-ItemProperty ("$($_.PSPath)\Capabilities\" + (@("URLAssociations", "FileAssociations") | Select-Object -Index $Extension.Contains("."))) -ErrorAction SilentlyContinue).$Extension
  }
  $list | ForEach-Object {
    if ($_) {
      Set-ItemProperty HKCU:\Software\Microsoft\Windows\CurrentVersion\ApplicationAssociationToasts ($_ + "_" + $Extension) -Value 0 -Type DWord -ErrorAction SilentlyContinue | Out-Null
    }
  }
}

function Update-RegistryChanges {
  try { [SftaNative]::NotifyAssocChanged() } catch { }
}

function Test-AccessDeniedError {
  # True when an error (or any inner exception) is a registry access denial.
  param([Parameter(Mandatory)]$ErrorRecord)
  $e = $ErrorRecord.Exception
  while ($e) {
    if ($e -is [System.UnauthorizedAccessException] -or $e -is [System.Security.SecurityException]) { return $true }
    $e = $e.InnerException
  }
  return $false
}

function Get-DisableUcpdCommand {
  # One line to paste into an administrator PowerShell: loads this file and runs Disable-Ucpd,
  # independent of the current directory and the execution policy.
  $path = if ($script:SftaScriptPath) { $script:SftaScriptPath } else { '.\SFTA.ps1' }
  $quoted = "'" + $path.Replace("'", "''") + "'"
  "powershell -NoProfile -ExecutionPolicy Bypass -Command "". $quoted; Disable-Ucpd"""
}

function Get-BlockedWriteMessage {
  param([Parameter(Mandatory)][string]$Extension, [bool]$UcpdActive)
  $cause = if ($UcpdActive) { 'the UserChoice Protection Driver (UCPD) is loaded and protects it' } else { 'access to its UserChoice key was denied' }
  @"
Windows blocked changing the default for '$Extension': $cause. The default was not changed.
To change it anyway, either:
  1. choose it in Settings > Apps > Default apps, or
  2. open PowerShell as administrator (right-click Start > Terminal (Admin) or Windows PowerShell (Admin)), paste:
       $(Get-DisableUcpdCommand)
     answer Y, restart Windows, then run the same command you just ran again.
"@
}

function Get-SftaNow { [DateTime]::Now }

function Get-SftaAssocRoot {
  # FileExts for an extension (".pdf"), UrlAssociations for a protocol ("https").
  param([Parameter(Mandatory)][string]$Extension)
  if ($Extension.Contains('.')) { $script:FileExtsRoot } else { $script:UrlAssocRoot }
}

function Get-SftaChoiceProgId {
  # UserChoiceLatest first (the key Explorer reads once the scheme is active), then UserChoice.
  param([Parameter(Mandatory)][string]$KeyPath)
  $latest = (Get-ItemProperty "$KeyPath\UserChoiceLatest\ProgId" -Name ProgId -ErrorAction SilentlyContinue).ProgId
  if ($latest) { return $latest }
  (Get-ItemProperty "$KeyPath\UserChoice" -ErrorAction SilentlyContinue).ProgId
}

function Get-SftaAssociation {
  # The ProgId for one extension/protocol, or "name, ProgId" lines for all of them.
  param([Parameter(Mandatory)][string]$Root, [string]$Name)
  if ($Name) { return Get-SftaChoiceProgId -KeyPath "HKCU:\$Root\$Name" }
  Get-ChildItem "HKCU:\$Root\*" | ForEach-Object {
    $progId = Get-SftaChoiceProgId -KeyPath $_.PSPath
    if ($progId) { "$($_.PSChildName), $progId" }
  }
}

function Get-SftaProgIdForProgram {
  # The ProgId Register-FTA creates for a program: SFTA.<file name without spaces><.ext>
  param([Parameter(Mandatory)][string]$ProgramPath, [Parameter(Mandatory)][string]$Extension)
  "SFTA." + [System.IO.Path]::GetFileNameWithoutExtension($ProgramPath).Replace(" ", "") + $Extension
}

function Resolve-SftaProgId {
  # A path to an existing program file means "the ProgId Register-FTA made for it"; anything else
  # is a ProgId as given. Only an explicit path counts (C:\..., \\server\..., .\... or ..\...), and
  # it is checked literally, so a ProgId is never rewritten because the current directory happens
  # to contain a file or folder of the same name, or because it contains a wildcard character.
  param([Parameter(Mandatory)][string]$ProgIdOrPath, [Parameter(Mandatory)][string]$Extension)
  $explicitPath = [System.IO.Path]::IsPathRooted($ProgIdOrPath) -or $ProgIdOrPath -match '^\.{1,2}[\\/]'
  if ($explicitPath -and (Test-Path -LiteralPath $ProgIdOrPath -PathType Leaf)) {
    return Get-SftaProgIdForProgram -ProgramPath $ProgIdOrPath -Extension $Extension
  }
  return $ProgIdOrPath
}

function Get-LegacyTimestampHex {
  # The legacy hash covers the UserChoice key's write time at MINUTE precision (UTC FILETIME).
  param([Parameter(Mandatory)][long]$FileTimeTicks)
  $minute = $FileTimeTicks - ($FileTimeTicks % 600000000L)
  '{0:x8}{1:x8}' -f ($minute -shr 32), ($minute -band 0xFFFFFFFFL)
}

function Write-LegacyUserChoice {
  # Writes the legacy UserChoice key and returns its hash. The hash must match the minute in
  # which Windows records the key's write; hashing "now" first and writing a moment later fails
  # whenever the write crosses into the next minute (Windows then rejects and deletes the key).
  # So: write, read the key's real write time, and re-hash with it if the minute moved.
  param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$Extension,
    [Parameter(Mandatory)][string]$ProgId
  )
  $userExperience = Get-UserExperience
  $sid = Get-UclUserSid
  $keyPath = "$Root\$Extension\UserChoice"
  $fileTime = (Get-SftaNow).ToFileTime()
  for ($attempt = 1; $attempt -le 3; $attempt++) {
    $ts = Get-LegacyTimestampHex -FileTimeTicks $fileTime
    # InvariantCulture, NOT .ToLower(): culture-aware casing differs on some locales and corrupts
    # the hash. The inputs (extension, SID, ProgId, hex time, shell32 string) are ASCII in
    # practice, where invariant and ASCII-only lowercasing agree.
    $hash = Get-LegacyUserChoiceHash "$Extension$sid$ProgId$ts$userExperience".ToLowerInvariant()
    [SftaNative]::DeleteKey($keyPath)
    [Microsoft.Win32.Registry]::SetValue("HKEY_CURRENT_USER\$keyPath", "Hash", $hash)
    [Microsoft.Win32.Registry]::SetValue("HKEY_CURRENT_USER\$keyPath", "ProgId", $ProgId)
    $written = [SftaNative]::GetLastWriteTime($keyPath)
    if ($written -lt 0) { throw "Could not read the UserChoice write time for '$Extension'." }
    if ((Get-LegacyTimestampHex -FileTimeTicks $written) -eq $ts) { return $hash }
    Write-Verbose "Legacy UserChoice for '$Extension' was written in a later minute than hashed; re-hashing (attempt $attempt)."
    $fileTime = $written
  }
  throw "The legacy UserChoice write for '$Extension' kept crossing a minute boundary. Run the command again."
}

function Write-UserChoiceLatest {
  # Writes UserChoiceLatest and returns its hash. A denied write throws a clear message
  # before anything else (legacy key, toasts) has been touched.
  param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$Extension,
    [Parameter(Mandatory)][string]$ProgId,
    [switch]$SkipSelfCheck,
    [bool]$UcpdActive
  )
  if (-not $SkipSelfCheck) {
    $status = Test-UclSelfCheck -Quiet
    $native = [UserChoiceLatest]::NativeAvailable
    # The self-check already hashes with Windows' own encoder when it is present, so a mismatch
    # never means "only the managed encoder is off": the salt, field order or scheme differs.
    if ($status -eq 'mismatch') {
      throw "None of this machine's existing UserChoiceLatest hashes can be reproduced, so Windows is using a hash scheme this script does not implement (for example after an update). Nothing was written. Re-run with -SkipSelfCheck only if you know the hash is right."
    }
    elseif ($status -eq 'noentries' -and -not $native) {
      Write-Warning "No existing UserChoiceLatest entry to verify against and the native encoder is unavailable; writing with the managed algorithm (exact for all real associations)."
    }
  }
  $latestBase = "$Root\$Extension\UserChoiceLatest"
  $progIdKeyPath = "$latestBase\ProgId"
  try {
    # (Re)create the ProgId subkey; its fresh LastWriteTime is the hash timestamp.
    $null = New-Item -Path "HKCU:\$progIdKeyPath" -Force -ErrorAction Stop
    [Microsoft.Win32.Registry]::SetValue("HKEY_CURRENT_USER\$progIdKeyPath", "ProgId", $ProgId)
    $ticks = [SftaNative]::GetLastWriteTime($progIdKeyPath)
    if ($ticks -lt 0) { throw "Could not read UserChoiceLatest\ProgId timestamp for '$Extension'." }
    $hash = Get-UclHash -Assoc $Extension -ProgId $ProgId -FileTimeTicks $ticks
    [Microsoft.Win32.Registry]::SetValue("HKEY_CURRENT_USER\$latestBase", "Hash", $hash)
    [Microsoft.Win32.Registry]::SetValue("HKEY_CURRENT_USER\$latestBase", "ProgId", $ProgId)
  }
  catch {
    if (Test-AccessDeniedError $_) { throw (Get-BlockedWriteMessage -Extension $Extension -UcpdActive $UcpdActive) }
    throw
  }
  Write-Verbose "UserChoiceLatest written: $hash"
  return $hash
}

# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------
function Get-FTA {
  [CmdletBinding()]
  param([Parameter(Mandatory = $false)][String]$Extension)
  Get-SftaAssociation -Root $script:FileExtsRoot -Name $Extension
}

function Get-PTA {
  [CmdletBinding()]
  param([Parameter(Mandatory = $false)][String]$Protocol)
  Get-SftaAssociation -Root $script:UrlAssocRoot -Name $Protocol
}

function Set-FTA {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][String]$ProgId,
    [Parameter(Mandatory = $true)][Alias("Protocol")][String]$Extension,
    [String]$Icon,
    [switch]$SkipLegacy,
    [switch]$SkipSelfCheck
  )

  $ProgId = Resolve-SftaProgId -ProgIdOrPath $ProgId -Extension $Extension
  $root = Get-SftaAssocRoot -Extension $Extension

  $uclPlan = Get-UclWritePlan
  if ($uclPlan.Action -eq 'refuse') {
    throw "Cannot write UserChoiceLatest on this machine: $($uclPlan.Reason). Explorer reads only UserChoiceLatest here, so a legacy UserChoice write would be ignored. Nothing was written."
  }

  # Which types UCPD protects differs between builds (26300 blocks only http, https and .pdf),
  # so the list is a hint only; an actual block is reported when the write is denied.
  $ucpd = Get-UcpdState
  if ($ucpd.Active -and ($Extension -in $script:UcpdProtected)) {
    Write-Verbose "UCPD is loaded and '$Extension' is protected on some Windows builds; the write may be denied."
  }

  Write-Verbose "ProgId: $ProgId  Extension/Protocol: $Extension"

  # --- UserChoiceLatest first: if Windows denies it, nothing else has been touched yet ---
  $hash = $null
  if ($uclPlan.Action -eq 'write') {
    $hash = Write-UserChoiceLatest -Root $root -Extension $Extension -ProgId $ProgId -SkipSelfCheck:$SkipSelfCheck -UcpdActive $ucpd.Active
  }

  # --- Legacy UserChoice (for machines where UserChoiceLatest is not yet active) ---
  $legacyWritten = $false
  if (-not $SkipLegacy) {
    try {
      $legacyHash = Write-LegacyUserChoice -Root $root -Extension $Extension -ProgId $ProgId
      $legacyWritten = $true
      Write-Verbose "Legacy UserChoice written: $legacyHash"
    }
    catch {
      # With UserChoiceLatest written, the legacy key is a fallback only; without it, a denied
      # legacy write means the default did not change, which must not pass silently.
      if ($uclPlan.Action -eq 'skip' -and (Test-AccessDeniedError $_)) {
        throw (Get-BlockedWriteMessage -Extension $Extension -UcpdActive $ucpd.Active)
      }
      Write-Verbose "Legacy UserChoice write failed: $_"
    }
  }

  if ($uclPlan.Action -eq 'skip') {
    Write-Verbose "UserChoiceLatest not written ($($uclPlan.Reason)); the scheme is not active here, so legacy UserChoice is authoritative."
  }

  if (-not $hash -and -not $legacyWritten) {
    Write-Warning "Nothing was written for '$Extension': UserChoiceLatest is unavailable here ($($uclPlan.Reason)) and the legacy UserChoice write was skipped or failed."
  }
  else {
    # Toasts only once an association was actually written, so a denied write leaves no trace.
    Write-ApplicationAssociationToasts $ProgId $Extension
    if ($hash) { Write-Output "Set $Extension -> $ProgId (UserChoiceLatest hash $hash)" }
    else { Write-Output "Set $Extension -> $ProgId (legacy UserChoice; UserChoiceLatest is not active on this machine)" }
  }

  if ($Icon) {
    try { [Microsoft.Win32.Registry]::SetValue("HKEY_CURRENT_USER\SOFTWARE\Classes\$ProgId\DefaultIcon", "", $Icon) } catch { }
  }

  Update-RegistryChanges
}

function Set-PTA {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][String]$ProgId,
    [Parameter(Mandatory = $true)][String]$Protocol,
    [String]$Icon,
    [switch]$SkipLegacy,
    [switch]$SkipSelfCheck
  )
  Set-FTA -ProgId $ProgId -Extension $Protocol -Icon $Icon -SkipLegacy:$SkipLegacy -SkipSelfCheck:$SkipSelfCheck
}

function Register-FTA {
  [CmdletBinding()]
  param(
    [Parameter(Position = 0, Mandatory = $true)][ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })][String]$ProgramPath,
    [Parameter(Position = 1, Mandatory = $true)][Alias("Protocol")][String]$Extension,
    [Parameter(Position = 2, Mandatory = $false)][String]$ProgId,
    [Parameter(Position = 3, Mandatory = $false)][String]$Icon
  )
  if (-not $ProgId) { $ProgId = Get-SftaProgIdForProgram -ProgramPath $ProgramPath -Extension $Extension }
  $progCommand = """$ProgramPath"" ""%1"""
  try {
    [Microsoft.Win32.Registry]::SetValue("HKEY_CURRENT_USER\SOFTWARE\Classes\$Extension\OpenWithProgids", $ProgId, ([byte[]]@()), [Microsoft.Win32.RegistryValueKind]::None)
    [Microsoft.Win32.Registry]::SetValue("HKEY_CURRENT_USER\SOFTWARE\Classes\$ProgId\shell\open\command", "", $progCommand)
  }
  catch { throw "Register ProgId and ProgId Command FAILED" }
  Set-FTA -ProgId $ProgId -Extension $Extension -Icon $Icon
}

function Remove-FTA {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][Alias("ProgId")][String]$ProgramPath,
    [Parameter(Mandatory = $true)][String]$Extension
  )
  $ProgId = Resolve-SftaProgId -ProgIdOrPath $ProgramPath -Extension $Extension
  $root = Get-SftaAssocRoot -Extension $Extension

  foreach ($leaf in 'UserChoice', 'UserChoiceLatest\ProgId', 'UserChoiceLatest') {
    [SftaNative]::DeleteKey("$root\$Extension\$leaf")
  }
  # Only a ProgId Register-FTA created (SFTA.*) is ours to delete. Any other ProgId belongs to an
  # application (e.g. a per-user ChromeHTML or AppX... registration), so only the choice is cleared.
  if ($ProgId -like 'SFTA.*') {
    Remove-Item -LiteralPath "HKCU:\SOFTWARE\Classes\$ProgId" -Recurse -ErrorAction SilentlyContinue | Out-Null
    Remove-ItemProperty -LiteralPath "HKCU:\SOFTWARE\Classes\$Extension\OpenWithProgids" -Name $ProgId -ErrorAction SilentlyContinue | Out-Null
    Update-RegistryChanges
    Write-Output "Removed: $ProgId"
    return
  }
  Update-RegistryChanges
  Write-Output "Cleared the default for $Extension. '$ProgId' was not created by Register-FTA, so its registration was kept."
}

# ---------------------------------------------------------------------------
#  UserExperience string (legacy hash salt), read from shell32.dll.
# ---------------------------------------------------------------------------
function Get-UserExperience {
  $hardcoded = "User Choice set via Windows User Experience {D18B6DD5-6124-4341-9318-804003BAFA0B}"
  $search = "User Choice set via Windows User Experience"
  try {
    $p = [Environment]::GetFolderPath([Environment+SpecialFolder]::SystemX86) + "\Shell32.dll"
    $fs = [System.IO.File]::Open($p, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try { [Byte[]]$data = (New-Object System.IO.BinaryReader($fs)).ReadBytes(5mb) }
    finally { $fs.Dispose() }
    $s = [Text.Encoding]::Unicode.GetString($data)
    $p1 = $s.IndexOf($search)
    $p2 = $s.IndexOf("}", $p1)
    return $s.Substring($p1, $p2 - $p1 + 1)
  }
  catch { return $hardcoded }
}

function Get-SftaUsage {
  @"
SFTA.ps1 is a library: load it with a dot and a space, then call its functions.
    . .\SFTA.ps1
    Set-FTA <ProgId> <.ext>        Set-PTA <ProgId> <protocol>        Get-FTA <.ext>
    Register-FTA <program.exe> <.ext>                                 Test-UclSelfCheck
Turn off the UserChoice Protection Driver (protected types such as .pdf, http, https), in an
administrator PowerShell, then restart Windows:
    $(Get-DisableUcpdCommand)
Shortcut for Set-FTA without loading:
    .\SFTA.ps1 <ProgId> <.ext> [Icon]
"@
}

# If dot-sourced, the functions above are now available. If run directly as
#   .\SFTA.ps1 <ProgId> <Extension> [Icon]
# forward the positional arguments to Set-FTA for convenience. Anything else (no arguments,
# or a guess such as ".\SFTA.ps1 -Disable-Ucpd") prints the usage instead of silently exiting.
if ($MyInvocation.InvocationName -ne '.') {
  $looksLikeSwitch = @($args | Where-Object { "$_".StartsWith('-') }).Count -gt 0
  if ($args.Count -lt 2 -or $looksLikeSwitch) {
    [Console]::Error.WriteLine((Get-SftaUsage))
    exit 1
  }
  $icon = if ($args.Count -ge 3) { $args[2] } else { $null }
  Set-FTA -ProgId $args[0] -Extension $args[1] -Icon $icon
}
