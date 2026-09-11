# Rejected TLB parameter allocation experiment

Measured on iOS 27 24A5430a: 100.681 s to visible lock screen; no useful gain
against the same-disk control (98.278–100.180 s). Not enabled in production.
See `docs/re/tcg-boot-multicore-ios27.md` for evidence and limitations.

Encoding check (write output outside the checkout):

```sh
clang -Wall -Wextra -Werror -fsanitize=undefined,address \
  -I tools/re/experiments/tlb_page_payload \
  tools/re/experiments/tlb_page_payload/test.c -o /tmp/tlb-page-payload-test
/tmp/tlb-page-payload-test
```

To reproduce in an isolated QEMU checkout based on 7c223ce, apply `qemu.patch`
and copy `tlb-page-payload.h` to `accel/tcg/`. Build with assertions, then pin
the executable in a disposable boot manifest. The patch preserves the original
work scheduling and has an allocating fallback for unrepresentable parameters.
Do not treat the encoding test as a multicore TLB semantic test.
