# UI process state in `UI_OP19_DCP1`

The guest reached Early Boot Complete and kept the real UI daemons alive, but
the captured display remained black.  This is a process-state result, not a
claim that Setup Assistant rendered.

## Evidence

The t20 and t90 snapshots are the raw 12-GiB-at-a-time captures under
`/tmp/dvm/ui-state-UI_OP19_DCP1/t20/` and `t90/`; the accompanying process-path
scans are `t20.execpaths.txt` and `t90.execpaths.txt`.  The independent process
walk found these live records at both times:

| PID | process | observed state / location |
|---:|---|---|
| 34 | `runningboardd` | `p_stat=2` (`SRUN`), active record in the snapshot |
| 35 | `SpringBoard` | `p_stat=2` (`SRUN`), active record at `0101c0000000.bin+0x1aae8068`; name fields at `+0x1aae8564` and `+0x1aae8575` |
| 71 | `backboardd` | `p_stat=2` (`SRUN`), active record at `0101c0000000.bin+0x2c7955b8`; name fields at `+0x2c795ab4` and `+0x2c795ac5` |
| 110 | `usermanagerd` | live process record |

The SpringBoard and backboardd thread groups were also live.  SpringBoard's
group is at virtual `ffffffe4d0520d80` (physical dump offset
`0101c0000000.bin+0x1a900d80`); backboardd's is at virtual
`ffffffe4d04ee6c0` (physical offset `0101c0000000.bin+0x26c4a6c0`).  At t90,
SpringBoard had three IPC receive waits using continuation
`fffffff02aa8f658` and three workqueue waits using
`fffffff02afb0b48`.  Backboardd had seven IPC receive waits, four workqueue
waits, and one special wait at continuation `fffffff02ab2f618` with event
`fffffff027011654`.  These are normal parked waits, not proof of a crash;
their interpretation is documented in [boot-idle](boot-idle.md).

`keybagd` (PID 56) survived the selector-107 test.  PID 138 was absent from
both snapshots.  `IOMFB_FDR_Loader` (PID 105) was present as `SZOMB`, so its
exit is not evidence that the display stack produced a frame.  The process
path scans contain SpringBoard and backboardd paths, while strings for
`/Applications/Setup.app` and PurpleBuddy are only catalog/application-data
strings; no Setup/PurpleBuddy process record was found.

The full t20/t90 `oskcdata` scans (`t20.oskcdata.txt` and
`t90.oskcdata.txt`) contain no `OS_REASON` or crash-info record for these UI
processes.  The guest had no serial exit, critical-process, reboot, or first
XNU/SPTM panic before the capture.  The corresponding IOMFB trace shows the
display path reaching `D120`, `D586`, `D575`, `A411/A420/A421/A424/A428`, and
repeated `A385` polling; it does not show `A407`, `A408`, a surface-map call,
or swap submission.  The screendump was nevertheless all black.  Therefore
the evidence establishes live userspace and a quiescent UI, but not a genuine
Welcome/Hello frame.

