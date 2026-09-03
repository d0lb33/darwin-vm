# A385/A386 selector byte

`UnifiedPipeline2` has an exact two-way RPC selector, but forcing its alternate
side does not advance display submission.  The decisive run is
`UI_A385_A386_MUT1`, made from a fresh qcow child of
`/tmp/dvm/data-seed/sks-op09-complete-2.qcow2` with the same IOMFB level-4
outputs and `D120,D586,@A385,D575` callback sequence as `UI_OP19_DCP1`.

## Static contract

In the extracted `com.apple.driver.AppleMobileDispH17P-DCP`, the
`UnifiedPipeline2` virtual at `+0xab8` is `0xfffffff00918b450`
(`/tmp/dvm/cpp_appleclcd2_d120.txt:345`).  It loads the peer from
`self+0x5e88`, calls the peer virtual at `+0x328`, and branches on its Boolean
result at `0xfffffff00918b464-0xfffffff00918b48c`.  The true arm tail-calls the
pipeline virtual at `+0xc30` (`...b490-...b4d8`); the false arm queries peer
virtual `+0x318` and, when that is true, tail-calls pipeline virtual `+0xc28`
(`...b4dc-...b550`).  The pipeline vtable resolves those slots as:

| slot | implementation | RPC construction |
|---|---|---|
| `+0xc28` | `0xfffffff00917ef58` | constructs FourCC `A385` at `0xfffffff00917ef78-0xfffffff00917ef7c`, requests four output bytes, and returns output bit zero at `...ef94-...ef98` |
| `+0xc30` | `0xfffffff00917efa8` | constructs FourCC `A386` at `0xfffffff00917efc8-0xfffffff00917efcc`, requests four output bytes, and returns output bit zero at `...efe4-...efe8` |

The same kext contains a conditional writer at `0xfffffff00917b38c`: after
two preceding virtual predicates (`+0x6c0` and `+0x6b8`) succeed, it stores
byte `1` at object offset `0x1ae0` at `0xfffffff00917b418-0xfffffff00917b424`.
With this boot's `0x20000000` kext slide, the writer and branch breakpoints are
respectively `0xfffffff02917b424` and `0xfffffff02918b48c`.

## Live causal test

LLDB was attached before boot and the writer breakpoint was armed with
auto-continue.  It had zero natural hits during the 180-second run.  The
selector breakpoint ignored the pre-callback occurrence, so its first stop was
the next occurrence after `D575` had completed.  The transport chronology is:

- first `A385` and callback-barrier release at
  `/tmp/dvm/probe/UI_A385_A386_MUT1.stderr.log:2529-2534`;
- `D575` completion with status zero at `:2535-2539`;
- the next `A385` at `:2584-2587`;
- the first `A386` immediately after the mutation at `:2589-2593`.

At the post-`D575` stop, LLDB recorded `x0=0`,
`x19=0xffffffe2261bc000`, peer
`[x19+0x5e88]=0xffffffe140254000`, and byte
`peer+0x1ae0=0` (`/tmp/dvm/UI_A385_A386_MUT1.lldb.log:29-45`).  The full
kernel backtrace is at `:46-59`.  LLDB wrote only that byte to `1` and read it
back at `:60-64`.  On the next invocation of the same branch, `x0=1` and the
byte remained `1` (`:67-91`), with the same call chain at `:92-105`.

This is a causal result: changing `peer+0x1ae0` is sufficient to change the
`+0x328` predicate and select A386 instead of A385.  It does not establish the
real-world condition that should set the byte; the static writer breakpoint
did not execute naturally in this run.

## Negative display result and health controls

After the switch, the guest issued 5,305 `A386` requests and no other IOMFB RPC
method.  The complete model log contains zero `A407`, `A408`, `D589`, `D591`,
or `surface_map` records.  Thus A386 is the alternate poll branch, not by
itself a swap/submission trigger.

The storage and boot controls remained healthy: the guest rooted from
`disk1s1` at
`/tmp/dvm/probe/UI_A385_A386_MUT1.serial.log:313`, mounted Preboot, Data,
Hardware, and User at `:428`, `:488`, `:496`, and `:588`, and reached Early
Boot Complete at `:639`.  The log contains zero `Copying ` lines and zero first
`panic(cpu` lines.

QEMU screendump produced `/tmp/dvm/UI_A385_A386_MUT1.ppm` and the converted
`/tmp/dvm/UI_A385_A386_MUT1.png`.  The PPM SHA-256 is
`43d418d35e149ea7e071c60ecb4ce967addd0aa4fc2eff4c6b0276403ed3f7fb`;
it is byte-identical to the black `UI_OP19_DCP1-current.ppm` baseline.  The
selector change therefore altered neither the observed RPC progression nor
scanout.
