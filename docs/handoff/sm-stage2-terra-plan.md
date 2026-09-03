# Task: Identify the blocking SMMachine stage-2 operation

Repository: `/Users/jdolbe1/Downloads/darwin-vm`  
Model: Terra/high

Read `CLAUDE.md` and `AGENTS.md` completely before doing project work.

## Established evidence

The valid `ROOT_DCP_STATE3` run recorded:

- `IOMFB_A500`
- `AFK_CLIENT_STATE_EVENT(x1=0, x2=1)`
- `AFK_REQUEST_SM_STATE(x1=1)`
- Three `SMMACHINE_TRANSITION` entries
- Selector 79 timing out 30.1 seconds later
- Zero XNU panics

Evidence is in:

- `/tmp/dvm/ROOT_DCP_STATE3.lldb.log:1167-1194`
- `/tmp/dvm/ROOT_DCP_STATE3.postmortem.txt`
- `/tmp/dvm/probe/ROOT_DCP_STATE3.stderr.log:2604-2610`

The third transition object is `0xffffffe145ca85c0`; its `+0x20` field equals
the client's state-1 object at `+0x140`.

Static disassembly shows the transition's stage-2 indirect call at:

- Call: `0xfffffff008b8c4e8`
- Return: `0xfffffff008b8c4ec`

The blocked stack contains the return address at
`AppleFirmwareKit+0x3102c`, confirming that this indirect call does not return
for the third transition.

## Work

Modify only `tools/re/dcp_sm_machine_callbacks.py`.

1. Fix kernel return-breakpoint canonicalization. The existing expression:

   ```python
   lr & 0x0000ffffffffffff
   ```

   loses the kernel sign extension. Canonicalize kernel pointers using the low
   40 bits plus `0xfffffff000000000`, consistent with `kmem.kptr()`.

2. Add a breakpoint immediately before the stage-2 indirect call at static
   `0xfffffff008b8c4e8`.

3. Log:

   - Thread ID and `tpidr_el1`
   - `x0`, the operation object
   - `x1`, the state/context argument
   - `x8`
   - `x9`, the authenticated indirect target
   - Canonicalized `x9`
   - Useful words from the operation object and its vtable

4. Add a direct breakpoint at `0xfffffff008b8c4ec`, immediately after the
   indirect call. This is the reliable stage-2 return witness.

5. Run exactly one bounded probe:

   ```bash
   CALLBACKS=dcp_sm_machine_callbacks \
   GDB_PORT=1262 \
   SECS=180 \
   PROBE_STALL_SELECTOR=0x4f \
   PROBE_STALL_SECS=30 \
   AUTO_POSTMORTEM=0 \
   KEEP_GUEST=0 \
   tools/re/setup_gate_probe.sh DELEGATE_SM_STAGE2_1
   ```

## Expected result

Confirm whether:

- The stage-2 call fires three times.
- The stage-2 return fires for the first two calls.
- The third call has no return.
- The third call's canonicalized `x9` identifies the exact blocking
  AppleFirmwareKit or RTBuddy method.

Attribute the target with `tools/re/kc_text_map.py` and statically disassemble
it far enough to identify its wait, expected completion field, and producer.

Do not change QEMU behavior.

## Deliverable

Report:

- Exact stage-2 target address and kext offset
- First-two-return/third-no-return counts
- Operation-object fields for all three calls
- The blocking function's relevant disassembly
- The concrete field or event it waits on
- The function that writes or wakes that field, if identifiable
- Exact log lines and positive controls

Commit the callback repair and an evidence note. Never push.

## Escalate immediately if

- The exact target cannot be attributed.
- The breakpoint controls fail.
- A first XNU/SPTM panic appears.
- A QEMU/device-model behavior change is required.
- The producer requires broad SMMachine reversal.
- The third transition unexpectedly returns and selector 79 still blocks.

