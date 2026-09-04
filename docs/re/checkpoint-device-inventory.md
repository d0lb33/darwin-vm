# Checkpoint device inventory

This is the review table for every stateful processor or device model involved
in the current darwin machine.  The adjacent JSON file is the machine-readable
version and records the implementation location for each row.

| Object/device | State owner | Existing VMState | Required fields | Timers/BHs | Host resources | Post-load work |
|---|---|---|---|---|---|---|
| ARM vCPU / SPTM | `ARMCPU`, `CPUARMState` | Stock `vmstate_arm_cpu` | Architectural and exception state; timers; Apple IMP-DEF bank; SPRR; GXF/GL banks; PMC counters | Generic and WFxT virtual timers | None | Stock ARM rebuilds hflags/TLB; Apple subsection restores value-only banks |
| AIC | `DarwinAICState` | `vmstate_darwin_aic` | Config; IRQ config; hardware/software pending; masks; MMIO backing | None | Destination `qemu_irq` objects | Validate geometry and recompute CPU IRQ level |
| ASC / RTKit | One `DarwinASCState` per IOP | `vmstate_darwin_asc` | CPU/power state; mailbox controls and FIFOs; RTKit protocol, endpoint map/start state | Optional diagnostic virtual timer; no BH | Destination IRQs and personality callbacks | Validate FIFO/protocol bounds, migrate timer deadline, reassert mailbox IRQs |
| SEP / SKS | `DarwinSEPState` | None at start | Mailbox queues; endpoints and OOL DMA; shared memory/TXM; power/status; request counters | None | DART link and destination IRQs | Validate queues/DMA ranges, retain destination DART, reassert IRQs |
| ANS / NVMe | `DarwinANSState` | None at start | MMIO backing; NVMe controller registers; queue bases/sizes/indices/phases; interrupt mask; RTKit boot state | None; commands are synchronous | Reopened `BlockBackend`, SART link, IRQ | Validate queue geometry and derive level IRQ; no host AIO exists to replay |
| DART / IOMMU | One `DarwinDARTState` per DART | None at start | Stream enables; TCR/TTBR; protection/error; MMIO backing | None | Registry link and IRQ | Validate geometry; no cached translations exist, so authoritative tables are walked again |
| SART | One `DarwinSARTState` per SART | None at start | MMIO window backing | None | Layout pointers and memory regions | Validate geometry and reconstruct destination layout pointers |
| DCP personality | `DarwinDCP` | None at start | Message count; next EPIC interface ID | None | ASC, AFK, IOMFB and environment policy | Destination construction recreates pointers; section/config equality prevents mismatches |
| DCP AFK / EPIC | `DarwinAFK` and endpoint array | None at start | Endpoint handshake; tags/DVAs; ring geometry; producer/consumer cursors; counters | None | ASC/DART and callback pointers | Validate endpoints/rings and retain destination pointers; ring bytes are in migrated RAM |
| DCP IOMFB | `DarwinIOMFB` | None at start | Heap DVA; RPC count; callback cursor; in-flight/start/flag state | None | ASC/DART; environment-derived script/reply tables | Validate cursor against identical destination script and preserve exactly one in-flight callback |
| Framebuffer / input | `DarwinFBState` plus guest RAM | None at start | Pixels in migrated RAM; keyboard modifiers | None | Surface, console, input handler, UART | Reattach destination surface and force full redraw |
| UART | `Exynos4210UartState` | Stock `vmstate_exynos4210_uart` | Registers; FIFO; interrupt state | None | New socket/logfile character backend | Stock device load; restore argv supplies unique host paths |
| Sparse unimplemented MMIO | One `UnimpRegion` per arm-io range | None at start | Last-written address/value map | None | Hash tables and memory regions | Validate addresses and rebuild hash; discard diagnostic read counts |
| AMCC | `AMCCState` | None at start | Four CTRR lower/upper banks | None | None | None |
| Guest RAM / clock | Migration core | Stock RAM/global state | RAM blocks, runstate, virtual-clock offsets | All registered virtual timers | None | Restore remains paused until PC witness; terminated wall time does not advance virtual time |
| ANS disk generation | External immutable qcow2 overlay | Outside VMState | Exact block generation paired with stream | None | Host file and backing chain | Flush before migration, terminate source, make source read-only, create fresh child per restore |

AIC and ASC migration was applied after the owner explicitly authorized edits
to the shared sources.  The implementation is in QEMU commit `60e1fd0`.
