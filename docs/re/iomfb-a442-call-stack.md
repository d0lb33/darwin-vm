# A442 live call-stack classification

## Source metadata

The sample is iOS 27.0 beta 8 (24A5430a) for iPhone17,3 / t8140, booted
with the project kernel slide `+0x20000000`.  The stopped-guest registers and
backtrace below are the orchestrator's captured `UI_A442_LLDB1` debugger
output; `/tmp/dvm/UI_A442_LLDB1.lldb.log` contains only the later attach/setup
commands, not that stop, so it is not a durable witness for those registers.
Static code
comes from `firmware/bootkc`, its extracted `com.apple.kernel`, and the
`com.apple.iokit.IOMobileGraphicsFamily-DCP` fileset image; the local XNU
source checkout is `xnu-12377.1.9` (`/tmp/dvm/apple-xnu`).

## Summary

The A442 stop is on the return path of an IOKit external-method request, not
on a DCP surface-map or swap-submit call.  `IOMobileFramebufferAP` copies the
requested `0x808` bytes to the caller's supplied destination at
`0xfffffff00a0cabc0`, and the surrounding kernel path packages that result for
an `is_io_connect_method` client request.  This rules out an *in-kernel*
payload branch between A442 and the captured return, but does not establish
that the requesting userspace client ignores the returned payload.

## Frame and data-flow table

| Runtime PC | Unslid PC / image | Function or role | Evidence |
|---|---|---|---|
| `0xfffffff02a0cabc0` | `0xfffffff00a0cabc0`, `IOMobileGraphicsFamily-DCP+0x18b40` | A442 result-copy loop | The wrapper's `cbz x20`; it loads a byte from `x19` and stores it to `x21` until `x20` is zero (`firmware/bootkc`, disassembly at `0xfffffff00a0cabc0-0xa0cabd8`).  The live stop recorded `x19=0xffffffeecdd6b000`, `x20=0x808`, `x21=0xffffffeecdd69810`, all-zero result/status. |
| `0xfffffff02a0b7920` | `0xfffffff00a0b7920`, `IOMobileGraphicsFamily-DCP+0x58a0` | `IOMobileFramebufferAP` A442 dispatch return | The entry at `0xfffffff00a0b773c` forwards its original eight arguments to vtable `+0x9e8`; `0xfffffff00a0b7920` saves only returned `x0`, then returns at `0xfffffff00a0b7944-0xa0b795c`.  No instruction there reads the copied destination. |
| `0xfffffff02b28f100` | `0xfffffff00b28f100`, `com.apple.kernel.__TEXT_EXEC+0x82e100` | `IOUserClient::callExternalMethod`-equivalent dispatch, high-confidence source match | The three-argument body begins at `0xfffffff00b28f030`; it dispatches through a vtable at `+0x540` or `+0x5d0` (`0xfffffff00b28f0ac-0xa0b70fc`) and lands at `+0xd0` after the callee (`0xfffffff00b28f100`).  This matches the three-argument call made by `IOUserClient::callExternalMethod(uint32_t, IOExternalMethodArguments *)` in `apple-xnu/iokit/Kernel/IOUserClient.cpp:6583-6596`; the kernelcache is stripped, so an exact symbol name is unavailable. |
| `0xfffffff02b28f3a8` | `0xfffffff00b28f3a8`, `com.apple.kernel.__TEXT_EXEC+0x82e3a8` | External-method arguments wrapper | It is immediately after `bl 0xfffffff00b28f030` at `0xfffffff00b28f3a4`; the caller constructed an argument block at `sp+0x30` and passed `x0=[sp+0x20]`, `w1=[sp+0x2c]`, `x2=sp+0x30` (`0xfffffff00b28f39c-0xa0b28f3a8`). |
| `0xfffffff02ac24df4` | `0xfffffff00ac24df4`, `com.apple.kernel.__TEXT_EXEC+0x1c3df4` | `is_io_connect_method` request path, high-confidence source match | The call at `0xfffffff00ac24df0` targets `0xfffffff00b28f138`; its return code is written to the caller's result field at `0xfffffff00ac24df4`.  The body validates OOL sizes, creates descriptors, and uses `0x1000` caps (`0xfffffff00ac24c98-0xa0c24df4`), matching `is_io_connect_method` in `apple-xnu/iokit/Kernel/IOUserClient.cpp:5133-5229`, including its `client->callExternalMethod(selector, &args)` call. |
| `0xfffffff02aaeb330`, `0xfffffff02aa8e05c`, `0xfffffff02aaa3f54`, `0xfffffff02ac5ff84`, `0xfffffff02ac65d0c`, `0xfffffff02aa63574` | respectively `0xfffffff00aaeb330`, `0xfffffff00aa8e05c`, `0xfffffff00aaa3f54`, `0xfffffff00ac5ff84`, `0xfffffff00ac65d0c`, `0xfffffff00aa63574`; all `com.apple.kernel.__TEXT_EXEC` | Generic kernel IPC/external-method caller frames | They lie in the extracted kernel `__TEXT_EXEC.__text` range `0xfffffff00aa61000-0xfffffff00b38a4f0` (`rabin2 -S /tmp/dvm/kexts/com.apple.kernel`).  The cache has no symbol table (`nm -arch arm64e /tmp/dvm/kexts/com.apple.kernel` returns zero symbols), so assigning individual symbol names would be unverified. |

## What the live arguments establish

At the stopped A442 copy loop, `x20=0x808` agrees with the A442 input field
at wire offset `+0x54`: the corresponding trace logs `08 08 00 00 00 00 00 00`
at `UI_OP19_DCP1.stderr.log:8613`.  `x26=0x4c` was preserved by the wrapper
from its original `w2` (`mov x26,x2` at `0xfffffff00a0caa84`); the same call's
wire descriptor has little-endian `4c 00 00 00` at `+0x08`
(`UI_OP19_DCP1.stderr.log:8609`).  The wrapper reports status from temporary
result `+0x1000` after the copy (`ldr w0,[x19,#0x1000]` at
`0xfffffff00a0cabdc`), so the observed zero result produces a zero kernel
return without a local payload check.

The captured stack therefore narrows the immediate consumer: A442 is serving
an external-method result buffer that crosses the IOKit client boundary.  It
does **not** prove that A442 is irrelevant to the Welcome screen: userspace
can inspect the copied `0x808` bytes after the external method returns, and
the current debugger capture did not retain the caller process or a
post-return read of `0xffffffeecdd69810`.

## Open questions

| Question | Observation that would settle it |
|---|---|
| Which process and IOConnect selector issued this A442? | At the next stop, select the `0xfffffff00ac24df4` frame and record the connection/client object, selector (`w1` before `0xfffffff00b28f138`), and current task/process. |
| Does userspace read the returned 0x808-byte payload? | Stop after `is_io_connect_method` returns to its IPC caller, record the user output address, then set a read watchpoint or trace the client-side `IOConnectCall*` return path. |
| Does a valid nonzero payload unlock `A407`/`A408`? | Preserve status word zero, vary only a field whose userspace consumer has been observed, and compare the first surface-map/swap RPC against the all-zero control. |

## Reproduction commands

```sh
# Map the two DCP PCs and inspect the decisive call/return edges.
rabin2 -S /tmp/dvm/kexts_all/com.apple.iokit.IOMobileGraphicsFamily-DCP
r2 -q -c 's 0xfffffff00a0b773c; pd 140; s 0xfffffff00a0caa4c; pd 110' \
  /tmp/dvm/kexts_all/com.apple.iokit.IOMobileGraphicsFamily-DCP

# Inspect the kernel external-method return path.
r2 -q -c 's 0xfffffff00ac24c68; pd 160; s 0xfffffff00b28f030; pd 96' \
  /tmp/dvm/kexts/com.apple.kernel
```
