# Standalone Hypervisor.framework probe (no QEMU). Ad-hoc signed with the
# hypervisor entitlement, which needs no sudo and no Apple developer account.
CC      ?= clang
CFLAGS  ?= -O1 -g -Wall -Wextra -std=c11
LDFLAGS ?= -framework Hypervisor

all: hvf_probe

hvf_probe: hvf_probe.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)
	codesign -s - --entitlements hvf.entitlements -f $@

# How much does one VM exit cost on this Mac? Feeds docs/re/hvf-acceleration.md.
exitbench: hvf_exitbench

hvf_exitbench: hvf_exitbench.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)
	codesign -s - --entitlements hvf.entitlements -f $@

# Cross-check the hand-assembled encodings against clang's assembler.
check: hvf_probe check.o
	./hvf_probe --selftest > selftest.txt
	xcrun llvm-objdump -d check.o | awk '/^ *[0-9a-f]+:/ { printf "%s\n", $$2 }' > check.txt
	diff -u check.txt selftest.txt && echo "encodings match clang"

check.o: check.S
	$(CC) -c -arch arm64 -march=armv8.5-a+memtag -o $@ $<

run: hvf_probe
	./hvf_probe | tee results.txt

clean:
	rm -f hvf_probe check.o check.txt selftest.txt
	rm -rf hvf_probe.dSYM

.PHONY: all check run clean
